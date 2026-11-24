#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ssh_shot.py —— SSH 远程执行命令 + 输出截图取证

用途: 给一组 SSH 凭据和命令, 逐条在远端执行, 把每条命令的完整输出(含 ANSI 颜色)
      交给 termshot 渲染成终端风格截图, 再按需转成 png/jpg/jpeg/webp/bmp,
      并生成 manifest.json 供审计/取证归档。

依赖:
    paramiko           (pip install paramiko)
    Pillow >= 10       (非 PNG 格式需要, pip install 'Pillow>=10')
    termshot >= 0.4    (go install github.com/homeport/termshot/cmd/termshot@latest
                        或 brew install homeport/tap/termshot)

用法示例:
    # 单条命令
    ./ssh_shot.py --host 10.0.0.5 -p 'pass' -c "uname -a"

    # 多条命令
    ./ssh_shot.py --host 10.0.0.5 --user root -p 'pass' \
        -c "clear; hostname" -c "cat /etc/passwd | head -20"

    # 命令清单文件(格式见 commands.sample.json)
    ./ssh_shot.py --host 10.0.0.5 -p 'pass' --commands-json commands.sample.json -o ./evidence

    # 私钥登录 + 自定义终端宽度
    ./ssh_shot.py --host 10.0.0.5 -i ~/.ssh/id_ed25519 --cols 160 -c "systemctl status sshd"

    # 批量服务器: 清单每行 `ip user password`, 输出目录只有图片 {ip}_{目录名}
    ./ssh_shot.py --hosts-file hosts.txt --commands-json cmds.json -o ./巡检报告

退出码:
    0 全部成功    1 部分命令失败/超时    2 参数或环境错误(如 termshot 缺失)
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from getpass import getpass
from pathlib import Path

try:
    import paramiko
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "[!] 缺少 paramiko。请在虚拟环境里安装:\n"
        "    uv venv .venv && uv pip install paramiko\n"
        "    然后使用 .venv/bin/python ssh_shot.py ...\n"
    )
    raise SystemExit(2)

DEFAULT_TERMSHOT_CANDIDATES = (
    "~/go/bin/termshot",
    "/usr/local/bin/termshot",
    "/opt/homebrew/bin/termshot",
    "/usr/bin/termshot",
)

# 用户可选输出格式 -> (文件后缀, termshot/Pillow 实际格式)
# termshot 只能出 PNG, 所以先渲染到临时 PNG, 再由 Pillow 转目标格式。
IMAGE_FORMATS = {
    "png": (".png", "PNG"),
    "jpg": (".jpg", "JPEG"),
    "jpeg": (".jpg", "JPEG"),
    "webp": (".webp", "WEBP"),
    "bmp": (".bmp", "BMP"),
}

# 自适应列数范围: 下限避免短输出字过大, 上限防止单行过长导致画面过宽
MIN_COLS, MAX_COLS = 45, 120

# 终端里需要清掉、但 termshot 解析不了的转义序列(OSC / DCS / 私有模式等)
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_DCS_RE = re.compile(r"\x1b[P^_][^\x1b]*\x1b\\")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")
_SGR_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class Task:
    """一条待执行的命令, 或一个待执行的脚本。"""

    id: str
    name: str
    command: str
    timeout: int = 0  # 0 表示使用全局 timeout
    local_script: str = ""   # 本地脚本绝对路径(需要上传)
    remote_script: str = ""  # 远端实际执行的脚本路径
    interpreter: str = ""    # 解释器, 如 bash / python3 / "/bin/bash -e"
    sha256: str = ""         # 脚本内容指纹(本地文件或远端拉取内容)
    uploaded: bool = False   # 是否由本程序上传
    evidence_script: str = ""  # 存证副本在证据目录中的文件名

    @property
    def safe_id(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.id).strip("_") or "row"

    @property
    def is_script(self) -> bool:
        return bool(self.local_script or self.remote_script)


@dataclass
class Result:
    """一条命令的执行结果。"""

    id: str
    name: str
    command: str
    exit_status: int | None = None
    duration: float = 0.0
    output_bytes: int = 0
    ansi_file: str = ""
    image_file: str = ""
    columns: int = 0
    error: str = ""
    collected_at: str = ""   # 采集时刻, 用于图片头部与审计回溯
    timed_out: bool = False
    local_script: str = ""
    remote_script: str = ""
    interpreter: str = ""
    sha256: str = ""
    evidence_script: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.timed_out


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def find_termshot(explicit: str | None) -> str | None:
    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    hit = shutil.which("termshot")
    if hit:
        return hit
    for cand in DEFAULT_TERMSHOT_CANDIDATES:
        p = Path(cand).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def sanitize_output(text: str, keep_cr: bool) -> str:
    """清掉 termshot 无法解析的控制序列, 统一换行。"""
    text = _OSC_RE.sub("", text)
    text = _DCS_RE.sub("", text)
    text = text.replace("\r\n", "\n")
    if not keep_cr:
        text = text.replace("\r", "")
    text = _CTRL_RE.sub("", text)
    # 去掉尾部空白行, 但保留一个有内容的结尾
    return text.rstrip("\n") + "\n"


def build_header(task: Task, user: str, host: str, collected_at: str = "") -> str:
    """自描述头: 保留命令原文(提示符)与脚本来源, 不额外加行号标签。"""
    prompt = f"{user}@{host}:~$ {task.command}"
    head = (
        f"\x1b[1;32m{user}@{host}\x1b[0m:\x1b[1;34m~\x1b[0m$ "
        f"\x1b[1;37m{task.command}\x1b[0m\n"
    )
    if collected_at:
        head += f"\x1b[2m# collected at {collected_at}  host: {user}@{host}\x1b[0m\n"
    if task.sha256:
        origin = task.local_script or task.remote_script
        kind = "uploaded" if task.uploaded else "remote"
        head += (
            f"\x1b[2m# {kind}: {origin}  sha256: {task.sha256[:32]}\u2026\x1b[0m\n"
        )
    head += f"\x1b[2m{'─' * min(60, max(20, len(prompt)))}\x1b[0m\n"
    return head


def build_footer(res: Result) -> str:
    if res.timed_out:
        return f"\x1b[1;33m[timeout after {res.duration:.1f}s]\x1b[0m\n"
    if res.error:
        return f"\x1b[1;31m[error: {res.error}]\x1b[0m\n"
    color = "32" if res.exit_status == 0 else "31"
    return f"\x1b[1;{color}m[exit {res.exit_status}]  ({res.duration:.2f}s)\x1b[0m\n"


def build_aggregate_footer(results: list[Result]) -> str:
    """--merge 用: 整批命令只输出一个汇总 footer(最差状态 + 总耗时)。"""
    total = sum(r.duration for r in results)
    if any(r.timed_out for r in results):
        return f"\x1b[1;33m[timeout after {total:.1f}s]\x1b[0m\n"
    errored = next((r for r in results if r.error), None)
    if errored:
        return f"\x1b[1;31m[error: {errored.error}]\x1b[0m\n"
    bad = next((r.exit_status for r in results if r.exit_status not in (0, None)), 0)
    color = "32" if bad == 0 else "31"
    return f"\x1b[1;{color}m[exit {bad}]  ({total:.2f}s)\x1b[0m\n"


# --------------------------------------------------------------------------- #
# 脚本支持
# --------------------------------------------------------------------------- #
SCRIPT_EXT_INTERP = {
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "zsh",
    ".ksh": "ksh",
    ".py": "python3",
    ".pl": "perl",
    ".rb": "ruby",
}
SCRIPT_FETCH_LIMIT = 1024 * 1024 * 4  # 拉取/上传脚本大小的安全上限


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def infer_interpreter(explicit: str | None, path_hint: str, shebang: str = "") -> str:
    """推导解释器: --shell > 扩展名 > shebang > bash。"""
    if explicit:
        return explicit
    ext = Path(path_hint).suffix.lower()
    if ext in SCRIPT_EXT_INTERP:
        return SCRIPT_EXT_INTERP[ext]
    line = shebang.strip()
    if line.startswith("#!"):
        parts = line[2:].split()
        if parts:
            if parts[0].rsplit("/", 1)[-1] == "env" and len(parts) > 1:
                rest = [p for p in parts[1:] if not p.startswith("-")]
                return " ".join(rest) if rest else "bash"
            return " ".join(parts)
    return "bash"


def remote_exec_command(task: Task) -> str:
    """拼出远端真正执行的命令行。"""
    quoted = shlex.quote(task.remote_script)
    return f"{task.interpreter} {quoted}" if task.interpreter else quoted


def ensure_remote_dir(sftp: paramiko.SFTPClient, path: str) -> None:
    """类似 mkdir -p。"""
    parts = [p for p in path.strip("/").split("/") if p]
    cur = "/" if path.startswith("/") else ""
    for part in parts:
        cur = f"{cur}{part}" if cur.endswith("/") or cur == "" else f"{cur}/{part}"
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def upload_script(sftp: paramiko.SFTPClient, local: Path, remote_dir: str) -> str:
    """上传脚本并 chmod 700, 返回远端路径。"""
    ensure_remote_dir(sftp, remote_dir)
    remote = f"{remote_dir.rstrip('/')}/{local.name}"
    sftp.put(str(local), remote)
    sftp.chmod(remote, 0o700)
    return remote


def read_remote_head(sftp: paramiko.SFTPClient, path: str, size: int = 200) -> str:
    try:
        with sftp.open(path, "rb") as fh:
            head = fh.read(size).decode("utf-8", errors="replace")
        lines = head.splitlines()
        return lines[0] if lines else ""
    except Exception:  # noqa: BLE001
        return ""


def fetch_remote_script(sftp: paramiko.SFTPClient, path: str) -> bytes:
    """把远端脚本拉回本地存证; 超过上限则截断。"""
    with sftp.open(path, "rb") as fh:
        return fh.read(SCRIPT_FETCH_LIMIT)


# --------------------------------------------------------------------------- #
# 命令清单
# --------------------------------------------------------------------------- #
def load_tasks(args: argparse.Namespace) -> list[Task]:
    tasks: list[Task] = []

    if args.commands_json:
        path = Path(args.commands_json)
        if not path.is_file():
            raise SystemExit(f"[!] 命令清单不存在: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"[!] 命令清单 JSON 解析失败: {exc}")
        if isinstance(raw, dict):
            raw = raw.get("commands", [])
        if not isinstance(raw, list):
            raise SystemExit("[!] 命令清单应为数组, 或 {\"commands\": [...]}")
        for i, item in enumerate(raw, 1):
            if isinstance(item, str):
                tasks.append(Task(id=f"row{i:02d}", name="", command=item))
                continue
            if not isinstance(item, dict) or "command" not in item:
                raise SystemExit(f"[!] 命令清单第 {i} 项缺少 command 字段")
            tasks.append(
                Task(
                    id=str(item.get("id") or f"row{i:02d}"),
                    name=str(item.get("name") or ""),
                    command=str(item["command"]),
                    timeout=int(item.get("timeout") or 0),
                )
            )

    for cmd in args.command or []:
        tasks.append(Task(id=f"row{len(tasks) + 1:02d}", name="", command=cmd))

    for script in getattr(args, "script", None) or []:
        path = Path(script).expanduser()
        if not path.is_file():
            raise SystemExit(f"[!] 本地脚本不存在: {path}")
        if path.stat().st_size > SCRIPT_FETCH_LIMIT:
            raise SystemExit(f"[!] 脚本过大(> {SCRIPT_FETCH_LIMIT} 字节): {path}")
        data = path.read_bytes()
        first_line = data.decode("utf-8", errors="replace").splitlines()[:1]
        tasks.append(
            Task(
                id=f"row{len(tasks) + 1:02d}",
                name=f"script {path.name}",
                command="",
                local_script=str(path.resolve()),
                interpreter=infer_interpreter(args.shell, path.name, first_line[0] if first_line else ""),
                sha256=sha256_bytes(data),
            )
        )

    for script in getattr(args, "remote_script", None) or []:
        tasks.append(
            Task(
                id=f"row{len(tasks) + 1:02d}",
                name=f"script {Path(script).name}",
                command="",
                remote_script=script,
                interpreter=args.shell or "",  # 稍后连上后再按 shebang 推导
            )
        )

    if not tasks:
        raise SystemExit("[!] 没有命令可执行: 用 -c / --commands-json / --script / --remote-script 指定")

    for i, t in enumerate(tasks, 1):
        if not t.id or t.id == f"row{i:02d}":
            t.id = f"row{i:02d}"
        if not t.name and t.command:
            t.name = t.command.strip().splitlines()[0][:48]
    return tasks


# --------------------------------------------------------------------------- #
# SSH
# --------------------------------------------------------------------------- #
def connect(args: argparse.Namespace) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    password = args.password
    if password is None and not args.key:
        try:
            password = getpass(f"{args.user}@{args.host} 的密码: ")
        except EOFError:
            raise SystemExit("[!] 未提供密码且无法交互输入, 请用 -p 或 -i 指定凭据")
        if not password:
            raise SystemExit("[!] 密码为空, 请用 -p/--key 明确指定认证方式")

    passphrase = args.key_passphrase
    if args.key and passphrase is None and args.ask_key_passphrase:
        try:
            passphrase = getpass("私钥口令: ")
        except EOFError:
            raise SystemExit("[!] 无法交互输入私钥口令")

    kwargs = dict(
        hostname=args.host,
        port=args.port,
        username=args.user,
        timeout=args.connect_timeout,
        banner_timeout=args.connect_timeout,
        auth_timeout=args.connect_timeout,
        allow_agent=not args.no_agent,
        look_for_keys=not (args.password or args.no_agent),
    )
    if password:
        kwargs["password"] = password
    if args.key:
        kwargs["key_filename"] = str(Path(args.key).expanduser())
        if passphrase is not None:
            kwargs["passphrase"] = passphrase

    try:
        client.connect(**kwargs)
    except paramiko.AuthenticationException:
        raise SystemExit("[!] 认证失败: 账号/密码/私钥不正确")
    except paramiko.SSHException as exc:
        raise SystemExit(f"[!] SSH 连接失败: {exc}")
    except (socket.error, OSError) as exc:
        raise SystemExit(f"[!] 无法连接到 {args.host}:{args.port} -> {exc}")
    return client


def run_task(client: paramiko.SSHClient, task: Task, args: argparse.Namespace) -> tuple[Result, str]:
    """执行一条命令, 返回 (结果, 原始输出文本)。"""
    res = Result(id=task.id, name=task.name, command=task.command)
    res.local_script = task.local_script
    res.remote_script = task.remote_script
    res.interpreter = task.interpreter
    res.sha256 = task.sha256
    res.evidence_script = task.evidence_script
    timeout = task.timeout or args.timeout
    started = time.monotonic()
    try:
        chan = client.get_transport().open_session(timeout=args.connect_timeout)
        if not args.no_pty:
            chan.get_pty(term=args.term, width=args.cols, height=args.rows)
        chan.exec_command(task.command)
        chan.settimeout(0.2)

        deadline = started + timeout
        chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        eof = False
        while True:
            try:
                data = chan.recv(65536)
            except socket.timeout:
                data = None
            if data:
                chunks.append(data)
                continue
            if data == b"":
                eof = True
                break

            # 暂时无数据可读: 判断是否已结束 / 是否超时
            finished = chan.exit_status_ready() or chan.eof_received
            if finished:
                eof = True
                # 收尾宽限期: 远端往往先发 exit-status 再关流, 残留数据要抽干净
                grace_end = time.monotonic() + 0.4
                while time.monotonic() < grace_end:
                    moved = False
                    while chan.recv_ready():
                        chunks.append(chan.recv(65536))
                        moved = True
                    if args.no_pty:
                        while chan.recv_stderr_ready():
                            err_chunks.append(chan.recv_stderr(65536))
                            moved = True
                    if not moved and chan.eof_received:
                        break
                    time.sleep(0.02)
                break
            if time.monotonic() > deadline:
                res.timed_out = True
                res.error = "命令超时"
                break

        raw = b"".join(chunks)
        if err_chunks:
            raw += b"\n\x1b[1;31m[stderr]\x1b[0m\n" + b"".join(err_chunks)

        if res.timed_out:
            chan.close()
        elif eof:
            res.exit_status = chan.recv_exit_status()
    except (socket.timeout, TimeoutError):
        res.timed_out = True
        res.error = "命令超时"
        raw = b""
    except Exception as exc:  # noqa: BLE001 - 单条命令失败不应中断整体采集
        res.error = f"{type(exc).__name__}: {exc}"
        raw = b""

    res.duration = time.monotonic() - started
    res.output_bytes = len(raw)
    text = raw.decode("utf-8", errors="replace")
    return res, text


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def adaptive_columns(ansi_path: Path, explicit: int) -> int:
    """按 ANSI 输出里最长可见行算列数, 夹在 [MIN_COLS, MAX_COLS]; explicit>0 时手动覆盖。"""
    if explicit > 0:
        return explicit
    text = ansi_path.read_text(encoding="utf-8", errors="replace")
    text = _OSC_RE.sub("", text)
    text = _DCS_RE.sub("", text)
    text = _SGR_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    longest = max((len(line) for line in text.splitlines()), default=0)
    return max(MIN_COLS, min(MAX_COLS, longest + 2))


def detect_screen_px() -> int | None:
    """本机最大屏幕的物理像素宽度(优先); macOS/通用, Linux 回退 xrandr/xdpyinfo。"""
    try:  # macOS: system_profiler 报物理像素, Retina 下比 AppleScript 的逻辑点大一倍
        out = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                             capture_output=True, text=True, timeout=12).stdout
        widths = [int(m.group(1)) for m in re.finditer(r"Resolution:\s*(\d+)\s*x\s*\d+", out)]
        if widths:
            return max(widths)
    except Exception:  # noqa: BLE001
        pass
    try:  # macOS 逻辑点回退
        out = subprocess.run(
            ["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
            capture_output=True, text=True, timeout=12,
        ).stdout.strip()
        parts = [int(x.strip()) for x in out.split(",")]
        if len(parts) == 4 and parts[2] > 0:
            return parts[2]
    except Exception:  # noqa: BLE001
        pass
    try:  # Linux: xrandr 当前模式
        out = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=8).stdout
        widths = [int(m.group(1)) for m in re.finditer(r"(\d+)x\d+\s+[\d.]+\*", out)]
        if widths:
            return max(widths)
    except Exception:  # noqa: BLE001
        pass
    try:  # Linux: xdpyinfo
        out = subprocess.run(["xdpyinfo"], capture_output=True, text=True, timeout=8).stdout
        m = re.search(r"dimensions:\s*(\d+)x", out)
        if m:
            return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def detect_terminal_cols() -> int | None:
    """当前终端列数; 非 TTY 时读 COLUMNS, 都拿不到返回 None。"""
    env = os.environ.get("COLUMNS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    cols = shutil.get_terminal_size(fallback=(0, 0)).columns
    return cols if cols > 0 else None


def calibrate_px_per_col(termshot: str, args: argparse.Namespace, tmpdir: Path) -> tuple[float, float] | None:
    """渲染两个探针(20/40 列)求 px = slope*列数 + intercept, 抵消字体/版本差异。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    probe = tmpdir / ".calib.ansi"
    probe.write_text("X\n", encoding="utf-8")
    widths: list[int] = []
    try:
        for cols in (20, 40):
            png = tmpdir / f".calib{cols}.png"
            ok, _ = render(termshot, probe, png, cols, args)
            if not ok:
                return None
            with Image.open(png) as im:
                widths.append(im.size[0])
    except Exception:  # noqa: BLE001
        return None
    finally:
        for p in (probe, tmpdir / ".calib20.png", tmpdir / ".calib40.png"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    slope = (widths[1] - widths[0]) / 20
    return (slope, widths[0] - slope * 20) if slope > 0 else None


def resolve_target_columns(termshot: str | None, args: argparse.Namespace,
                           out_dir: Path) -> tuple[int | None, dict]:
    """按 --width-source 定列数; 返回 (列数或 None 表示逐图自适应, 写入清单的信息)。"""
    if args.render_columns > 0:
        return args.render_columns, {"source": "manual", "columns": args.render_columns}
    source = args.width_source
    term_cols = target_px = None
    if source == "terminal":
        term_cols = detect_terminal_cols()
        if not term_cols:
            target_px = detect_screen_px()
            if target_px:
                source = "screen(fallback)"
    else:  # screen
        target_px = detect_screen_px()
        if not target_px:
            term_cols = detect_terminal_cols()
            if term_cols:
                source = "terminal(fallback)"
    if term_cols:
        return term_cols, {"source": source, "terminal_cols": term_cols}
    if target_px:
        cal = calibrate_px_per_col(termshot, args, out_dir) if termshot else None
        if cal and cal[0] > 0:
            cols = round((target_px - cal[1]) / cal[0])
            px_per_col = round(cal[0], 2)
        else:  # 经验值兜底(本机实测 28.9px/列 + 97px 固定开销)
            cols = round((target_px - 97) / 28.9)
            px_per_col = None
        cols = max(MIN_COLS, min(500, cols))
        return cols, {"source": source, "screen_px": target_px,
                      "px_per_col": px_per_col, "columns": cols}
    return None, {"source": "content"}


def render(termshot: str, ansi_path: Path, png_path: Path, cols: int,
           args: argparse.Namespace) -> tuple[bool, str]:
    cmd = [
        termshot, "--raw-read", str(ansi_path), "--filename", str(png_path),
        "--columns", str(cols),
        "--clip-canvas", "--no-shadow",  # 终端框铺满画布, 无阴影、无外部背景
    ]
    if args.padding is not None:
        cmd += ["--padding", str(args.padding)]
    if args.no_decoration:
        cmd.append("--no-decoration")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.render_timeout)
    except subprocess.TimeoutExpired:
        return False, "termshot 渲染超时"
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"termshot 退出码 {proc.returncode}: {msg[-1] if msg else ''}"
    if not png_path.is_file() or png_path.stat().st_size == 0:
        return False, "termshot 未生成图片"
    return True, ""


def parse_color(spec: str) -> tuple[int, int, int] | None:
    """解析 #RRGGBB / #RGB / RRGGBB, 非法返回 None。"""
    s = (spec or "").strip().lower()
    hexpart = s[1:] if s.startswith("#") else s
    if len(hexpart) == 3:
        hexpart = "".join(ch * 2 for ch in hexpart)
    if len(hexpart) == 6:
        try:
            return tuple(int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    return None


def resolve_background(spec: str, rgba) -> tuple[int, int, int]:
    """给无 alpha 的格式挑底色: auto 取图上最常见的完全不透明色(termshot 是深灰)。"""
    s = (spec or "auto").strip().lower()
    if s == "auto":
        colors = rgba.getcolors(maxcolors=rgba.width * rgba.height) or []
        opaque = [(n, c) for n, c in colors if len(c) == 4 and c[3] == 255]
        if opaque:
            return max(opaque, key=lambda x: x[0])[1][:3]
        return (0, 0, 0)
    if s == "black":
        return (0, 0, 0)
    if s == "white":
        return (255, 255, 255)
    parsed = parse_color(s)
    if parsed is not None:
        return parsed
    raise ValueError(f"无法识别的底色: {spec!r} (可用 auto/black/white/#RRGGBB)")


def convert_image(src: Path, dst: Path, args: argparse.Namespace) -> tuple[bool, str]:
    """把 termshot 的临时 PNG 转成目标格式; 画布宽度已由 termshot 列数决定, 不再补背景。"""
    try:
        from PIL import Image
    except ImportError:
        return False, "缺少 Pillow, 请安装: uv pip install --python .venv/bin/python 'Pillow>=10'"
    fmt = IMAGE_FORMATS[args.image_format][1]
    try:
        with Image.open(src) as raw:
            rgba = raw.convert("RGBA")
            if fmt in ("PNG", "WEBP"):
                if fmt == "PNG":
                    rgba.save(dst, format="PNG")
                else:
                    rgba.save(dst, format="WEBP", quality=args.image_quality, method=6)
            else:
                bg = resolve_background(args.image_background, rgba)
                flat = Image.new("RGB", rgba.size, bg)
                flat.paste(rgba, mask=rgba.getchannel("A"))
                if fmt == "JPEG":
                    flat.save(dst, format="JPEG", quality=args.image_quality,
                              optimize=True, subsampling=0)
                else:
                    flat.save(dst, format="BMP")
    except Exception as exc:  # noqa: BLE001
        return False, f"图片转换失败: {exc}"
    if not dst.is_file() or dst.stat().st_size == 0:
        return False, "图片转换未生成文件"
    return True, ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ssh_shot.py",
        description="SSH 远程执行命令并把每条输出渲染成终端风格截图取证 (png/jpg/jpeg/webp/bmp)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--host", default=None, help="目标主机 IP/域名 (与 --hosts-file 二选一)")
    p.add_argument("--hosts-file", default=None, metavar="FILE",
                   help="主机清单纯文本 (每行: ip user password); 批量执行同一批命令")
    p.add_argument("--port", type=int, default=22, help="SSH 端口 (默认 22)")
    p.add_argument("--user", "-u", default="root", help="SSH 用户名 (默认 root)")
    p.add_argument("--password", "-p", default=None, help="SSH 密码; 不传则交互输入")
    p.add_argument("--key", "-i", default=None, help="私钥路径")
    p.add_argument("--key-passphrase", default=None, help="私钥口令")
    p.add_argument("--no-agent", action="store_true", help="不使用 ssh-agent")

    p.add_argument("-c", "--command", action="append", help="要执行的命令, 可重复")
    p.add_argument("--commands-json", help="命令清单 JSON 文件 (见 commands.sample.json)")
    p.add_argument("--script", action="append", metavar="LOCAL.sh",
                   help="本地 shell 脚本: 上传到远端后执行并截图, 可重复")
    p.add_argument("--remote-script", action="append", metavar="REMOTE.sh",
                   help="远端已存在的脚本路径: 直接执行并截图, 可重复")
    p.add_argument("--shell", default=None,
                   help="解释器 (如 bash / sh / python3 / '/bin/bash -e'); 默认按扩展名或 shebang 推导")
    p.add_argument("--remote-dir", default=None,
                   help="上传脚本的远端目录 (默认 /tmp/ssh_shot_<时间戳>)")
    p.add_argument("--keep-remote", action="store_true",
                   help="保留远端上传的脚本与目录 (默认采集完删除)")
    p.add_argument("--no-fetch-script", action="store_true",
                   help="不把远端脚本拉回证据目录存证")

    p.add_argument("-o", "--output", default="./evidence", help="证据输出根目录 (默认 ./evidence)")
    p.add_argument("--cols", type=int, default=200, help="远端 PTY 列宽 (默认 200)")
    p.add_argument("--rows", type=int, default=60, help="远端 PTY 行高 (默认 60)")
    p.add_argument("--no-pty", action="store_true", help="不申请 PTY (无颜色, 但不会因分页卡住)")
    p.add_argument("--term", default="xterm-256color", help="PTY TERM 类型 (默认 xterm-256color)")
    p.add_argument("--timeout", type=int, default=60, help="单条命令超时秒数 (默认 60)")
    p.add_argument("--connect-timeout", type=float, default=15.0, help="SSH 连接超时秒数")
    p.add_argument("--keep-cr", action="store_true", help="保留 \\r (默认转换为 \\n)")

    p.add_argument("--termshot", default=None, help="termshot 可执行文件路径")
    p.add_argument("--no-render", action="store_true", help="只保存 .ansi 文本, 不出图")
    p.add_argument("--render-columns", type=int, default=0,
                   help="手动指定列数 (0=按 --width-source 决定)")
    p.add_argument("--width-source", choices=("screen", "terminal"), default="screen",
                   help="宽度基准: screen=屏幕物理像素(默认), terminal=当前终端列数")
    p.add_argument("--merge", action="store_true",
                   help="多条命令按序执行后, 把输出拼成一张图(不生成单条图)")
    p.add_argument("--padding", type=int, default=None, help="图片内边距像素")
    p.add_argument("--no-decoration", action="store_true", help="不画窗口标题栏按钮")
    p.add_argument("--render-timeout", type=int, default=90, help="单张渲染超时秒数")
    p.add_argument("--image-format", choices=sorted(IMAGE_FORMATS), default="jpeg",
                   help="截图格式: png/jpg/jpeg/webp/bmp (默认 jpeg; jpg 与 jpeg 同为 .jpg)")
    p.add_argument("--image-quality", type=int, default=90,
                   help="有损格式质量 1-100, 默认 90 (对 jpg/jpeg/webp 生效)")
    p.add_argument("--image-background", default="auto",
                   help="图片底色: auto/black/white/#RRGGBB (默认 auto, 取截图自带的深色)")
    p.add_argument("--ask-key-passphrase", action="store_true", help="使用私钥时交互索要口令")
    return p


def parse_hosts_file(path: str) -> list[tuple[str, str, str]]:
    """解析纯文本主机清单: 每行 `ip user password`, 忽略空行与 # 注释。"""
    p = Path(path).expanduser()
    if not p.is_file():
        raise SystemExit(f"[!] 主机清单不存在: {p}")
    hosts: list[tuple[str, str, str]] = []
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise SystemExit(
                f"[!] 主机清单第 {lineno} 行格式错误 (应为: ip user password): {raw!r}"
            )
        hosts.append((parts[0], parts[1], parts[2]))
    if not hosts:
        raise SystemExit(f"[!] 主机清单为空: {p}")
    return hosts


def run_batch(args: argparse.Namespace) -> int:
    """批量模式: 对清单里每台主机执行同一批命令, 每主机只留一张图, 命名为 {ip}_{目录名}。"""
    hosts = parse_hosts_file(args.hosts_file)
    out_root = Path(args.output).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)  # 不存在则新建
    dir_name = out_root.name or out_root.resolve().name  # 图片名中的“目录名”
    ext = IMAGE_FORMATS[args.image_format][0]

    eprint(f"[*] 批量模式: {len(hosts)} 台主机, 输出到 {out_root} (图片名: {{ip}}_{dir_name}{ext})")
    ok_hosts = failed_hosts = 0
    for ip, user, password in hosts:
        eprint(f"\n===== {ip} ({user}) =====")
        # 每台主机先在临时目录里跑完整采集, 再把最终图片搬到 out_root, 删除临时目录
        with tempfile.TemporaryDirectory(prefix="ssh_shot_batch_") as work:
            cfg = copy.copy(args)
            cfg.host = ip
            cfg.user = user
            cfg.password = password
            cfg.key = None  # 批量模式用清单里的密码认证
            cfg.merge = True  # 每主机强制拼成一张图
            cfg.hosts_file = None
            cfg.output = work
            try:
                rc = main(cfg)
            except SystemExit as exc:  # connect() 认证/连接失败会抛 SystemExit
                eprint(f"    [!] {ip} 连接失败: {exc}")
                rc = 2
            merged = None
            if rc == 0:
                candidates = list(Path(work).rglob(f"merged{ext}"))
                merged = candidates[0] if candidates else None
            if merged and merged.is_file():
                # 图片名 = {ip}_{目录名}; ip 里的特殊字符做文件名安全化
                safe_ip = re.sub(r"[^A-Za-z0-9._-]", "_", ip)
                target = out_root / f"{safe_ip}_{dir_name}{ext}"
                shutil.copyfile(merged, target)
                eprint(f"    [✓] {target}")
                ok_hosts += 1
            else:
                failed_hosts += 1
                eprint(f"    [!] {ip} 采集失败 (退出码 {rc}), 未生成图片")

    eprint(f"\n[*] 批量完成: 成功 {ok_hosts}/{len(hosts)}, 失败 {failed_hosts}")
    eprint(f"[*] 输出目录: {out_root} (仅含 {ok_hosts} 张图片)")
    return 0 if failed_hosts == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = argv if isinstance(argv, argparse.Namespace) else p.parse_args(argv)

    if args.hosts_file and args.host:
        p.error("--host 与 --hosts-file 不能同时使用")
    if not args.hosts_file and not args.host:
        p.error("必须指定 --host 或 --hosts-file")

    if not 1 <= args.image_quality <= 100:
        p.error("--image-quality 必须在 1-100 之间")
    if not args.no_render and args.image_format in ("jpg", "jpeg", "bmp"):
        bg = args.image_background.strip().lower()
        if bg not in ("auto", "black", "white") and parse_color(bg) is None:
            p.error("--image-background 需要 auto/black/white/#RRGGBB")
    if not args.no_render:
        try:
            import PIL  # noqa: F401
        except ImportError:
            p.error("出图需要 Pillow, 请先安装: uv pip install --python .venv/bin/python 'Pillow>=10'")

    if args.hosts_file:
        return run_batch(args)

    tasks = load_tasks(args)

    termshot = None
    if not args.no_render:
        termshot = find_termshot(args.termshot)
        if not termshot:
            eprint(
                "[!] 未找到 termshot, 本次只保存 .ansi 文本。安装方式:\n"
                "    go install github.com/homeport/termshot/cmd/termshot@latest\n"
                "    brew install homeport/tap/termshot\n"
                "    或用 --termshot /path/to/termshot 指定路径"
            )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output).expanduser() / f"{args.host}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    target_cols, width_info = resolve_target_columns(termshot, args, out_dir)
    if target_cols:
        eprint(f"[*] 宽度基准: {width_info} -> {target_cols} 列")
    else:
        eprint(f"[*] 宽度基准: 按输出自适应 ({MIN_COLS}-{MAX_COLS} 列)")

    eprint(f"[*] 连接 {args.user}@{args.host}:{args.port} ...")
    client = connect(args)
    eprint(f"[*] 已连接, 输出目录: {out_dir}")

    # ---------------- 脚本准备: 上传 / 探测 shebang ----------------
    remote_dir = args.remote_dir or f"/tmp/ssh_shot_{stamp}"
    sftp: paramiko.SFTPClient | None = None
    uploaded: list[str] = []
    created_dir = False

    def get_sftp() -> paramiko.SFTPClient:
        nonlocal sftp
        if sftp is None:
            sftp = client.open_sftp()
        return sftp

    script_tasks = [t for t in tasks if t.is_script]
    try:
        for task in script_tasks:
            if task.local_script:
                local = Path(task.local_script)
                eprint(f"[*] 上传脚本 {local.name} -> {remote_dir}/")
                task.remote_script = upload_script(get_sftp(), local, remote_dir)
                task.uploaded = True
                uploaded.append(task.remote_script)
                created_dir = True
                # 本地脚本原件也存一份到证据目录
                ev = out_dir / f"script.{task.safe_id}.{local.name}"
                ev.write_bytes(local.read_bytes())
                task.evidence_script = ev.name
                task.command = remote_exec_command(task)
            else:
                # 远端脚本: 先确认存在, 再用 shebang 推导解释器, 并拉回内容存证
                try:
                    get_sftp().stat(task.remote_script)
                except IOError:
                    raise SystemExit(f"[!] 远端脚本不存在或不可访问: {task.remote_script}")
                if not task.interpreter and not args.no_fetch_script:
                    task.interpreter = infer_interpreter(
                        args.shell, task.remote_script, read_remote_head(get_sftp(), task.remote_script)
                    )
                task.interpreter = task.interpreter or "bash"
                task.command = remote_exec_command(task)
                if not args.no_fetch_script:
                    try:
                        data = fetch_remote_script(get_sftp(), task.remote_script)
                        task.sha256 = sha256_bytes(data)
                        ev = out_dir / f"script.{task.safe_id}.{Path(task.remote_script).name}"
                        ev.write_bytes(data)
                        task.evidence_script = ev.name
                    except Exception as exc:  # noqa: BLE001
                        eprint(f"[!] 拉取远端脚本失败({task.remote_script}): {exc}")
    except Exception as exc:  # noqa: BLE001
        if sftp:
            sftp.close()
        client.close()
        raise SystemExit(f"[!] 脚本准备失败: {exc}")

    results: list[Result] = []
    merged_parts: list[str] = []
    try:
        for idx, task in enumerate(tasks, 1):
            eprint(f"[{idx}/{len(tasks)}] {task.id}: {task.command.strip().splitlines()[0][:70]}")
            collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            res, text = run_task(client, task, args)
            res.collected_at = collected_at
            body = build_header(task, args.user, args.host, collected_at)
            body += sanitize_output(text, args.keep_cr)

            # 单条 ansi 始终带自己的 footer(取证完整); merge 图稍后只在图尾放一个汇总 footer
            ansi_path = out_dir / f"{task.safe_id}.ansi"
            ansi_path.write_text(body + build_footer(res), encoding="utf-8")
            res.ansi_file = ansi_path.name
            if args.merge:
                merged_parts.append(body)
            elif termshot:
                tmp_png = out_dir / f".{task.safe_id}.termshot.png"
                cols = target_cols or adaptive_columns(ansi_path, args.render_columns)
                res.columns = cols
                ok, err = render(termshot, ansi_path, tmp_png, cols, args)
                if ok:
                    image_path = out_dir / f"{task.safe_id}{IMAGE_FORMATS[args.image_format][0]}"
                    ok, err = convert_image(tmp_png, image_path, args)
                    if ok:
                        res.image_file = image_path.name
                    else:
                        res.error = res.error or err
                        eprint(f"    [!] {err}")
                else:
                    res.error = res.error or err
                    eprint(f"    [!] 出图失败: {err}")
                try:
                    tmp_png.unlink(missing_ok=True)
                except OSError:
                    pass
            elif not args.no_render:
                res.error = res.error or "termshot 缺失"

            state = "ok" if res.ok else ("TIMEOUT" if res.timed_out else "FAIL")
            eprint(f"    -> {state}  exit={res.exit_status}  {res.duration:.2f}s  {res.output_bytes}B")
            results.append(res)

        # --merge: 所有命令输出按序拼成一个 ANSI, 只在图尾放一个汇总 footer
        if args.merge:
            merged_ansi = out_dir / "merged.ansi"
            merged_ansi.write_text(
                "".join(merged_parts) + build_aggregate_footer(results), encoding="utf-8"
            )
            if termshot:
                tmp_png = out_dir / ".merged.termshot.png"
                cols = target_cols or adaptive_columns(merged_ansi, args.render_columns)
                ok, err = render(termshot, merged_ansi, tmp_png, cols, args)
                if ok:
                    image_path = out_dir / f"merged{IMAGE_FORMATS[args.image_format][0]}"
                    ok, err = convert_image(tmp_png, image_path, args)
                    if ok:
                        for r in results:
                            r.columns = cols
                            r.image_file = image_path.name
                    else:
                        err = f"图片转换失败: {err}"
                eprint(f"    -> merged  exit=-  {cols} 列  {image_path.name if ok else err}")
                if not ok:
                    for r in results:
                        r.error = r.error or err
                try:
                    tmp_png.unlink(missing_ok=True)
                except OSError:
                    pass
            elif not args.no_render:
                for r in results:
                    r.error = r.error or "termshot 缺失"
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:  # noqa: BLE001
                pass
        # 清理远端上传的脚本(默认); 只看本程序自己上传的文件, 不动其他任何东西
        if (uploaded or created_dir) and not args.keep_remote:
            try:
                cl = client.open_sftp()
                for path in uploaded:
                    try:
                        cl.remove(path)
                    except IOError as exc:
                        eprint(f"[!] 远端清理失败 {path}: {exc}")
                if created_dir:
                    try:
                        cl.rmdir(remote_dir)
                    except IOError:
                        pass  # 目录里还有别的文件就不删
                cl.close()
            except Exception as exc:  # noqa: BLE001
                eprint(f"[!] 远端清理失败: {exc}")
        client.close()

    manifest = {
        "tool": "ssh_shot.py",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target": {"host": args.host, "port": args.port, "user": args.user},
        "render": {
            "termshot": termshot,
            "columns": args.render_columns or None,
            "pty": not args.no_pty,
            "cols": args.cols,
            "rows": args.rows,
            "format": args.image_format,
            "quality": args.image_quality if args.image_format in ("jpg", "jpeg", "webp") else None,
            "background": args.image_background,
            "clip_canvas": True,
            "shadow": False,
            "width_source": width_info,
            "merge": args.merge,
        },
        "scripts": {
            "remote_dir": remote_dir if uploaded else None,
            "kept": bool(args.keep_remote and uploaded),
            "uploaded": uploaded,
            "shell_override": args.shell,
        },
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.ok),
            "failed": sum(1 for r in results if not r.ok),
        },
        "results": [asdict(r) for r in results],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n证据目录: {out_dir}")
    print(f"清单文件: {manifest_path}")
    print(f"成功 {manifest['summary']['ok']}/{len(results)}, 失败 {manifest['summary']['failed']}")

    if not termshot and not args.no_render:
        return 2
    return 0 if manifest["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        eprint("\n[!] 已中断")
        raise SystemExit(130)
