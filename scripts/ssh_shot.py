#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ssh_shot.py —— SSH 远程执行命令 + 输出截图取证

用途: 给一组 SSH 凭据和命令, 逐条在远端执行, 把每条命令的完整输出(含 ANSI 颜色)
      交给 termshot 渲染成一张 PNG 截图, 并生成 manifest.json 供审计/取证归档。

依赖:
    paramiko           (pip install paramiko)
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

退出码:
    0 全部成功    1 部分命令失败/超时    2 参数或环境错误(如 termshot 缺失)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
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

# 终端里需要清掉、但 termshot 解析不了的转义序列(OSC / DCS / 私有模式等)
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_DCS_RE = re.compile(r"\x1b[P^_][^\x1b]*\x1b\\")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")


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
    png_file: str = ""
    error: str = ""
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


def build_header(task: Task, idx: int, total: int, user: str, host: str) -> str:
    """自描述头: 便于截图单独成证时也能看出是哪台机器、哪条命令。"""
    tag = f"[{task.id}] {task.name}"
    prompt = f"{user}@{host}:~$ {task.command}"
    head = (
        f"\x1b[1;36m{tag}  ({idx}/{total})\x1b[0m\n"
        f"\x1b[1;32m{user}@{host}\x1b[0m:\x1b[1;34m~\x1b[0m$ "
        f"\x1b[1;37m{task.command}\x1b[0m\n"
    )
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
def render(termshot: str, ansi_path: Path, png_path: Path, args: argparse.Namespace) -> tuple[bool, str]:
    cmd = [termshot, "--raw-read", str(ansi_path), "--filename", str(png_path)]
    if args.render_columns > 0:
        cmd += ["--columns", str(args.render_columns)]
    if args.margin is not None:
        cmd += ["--margin", str(args.margin)]
    if args.padding is not None:
        cmd += ["--padding", str(args.padding)]
    if args.no_shadow:
        cmd.append("--no-shadow")
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ssh_shot.py",
        description="SSH 远程执行命令并用 termshot 把每条输出渲染成 PNG 截图取证",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--host", required=True, help="目标主机 IP/域名")
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
    p.add_argument("--render-columns", type=int, default=0, help="出图时强制换行列数 (0=不强制)")
    p.add_argument("--margin", type=int, default=None, help="图片外边距像素")
    p.add_argument("--padding", type=int, default=None, help="图片内边距像素")
    p.add_argument("--no-shadow", action="store_true", help="不画窗口阴影")
    p.add_argument("--no-decoration", action="store_true", help="不画窗口标题栏按钮")
    p.add_argument("--render-timeout", type=int, default=90, help="单张渲染超时秒数")
    p.add_argument("--ask-key-passphrase", action="store_true", help="使用私钥时交互索要口令")
    args = p.parse_args(argv)

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

    eprint(f"[*] 连接 {args.user}@{args.host}:{args.port} ...")
    client = connect(args)
    out_dir.mkdir(parents=True, exist_ok=True)
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
    try:
        for idx, task in enumerate(tasks, 1):
            eprint(f"[{idx}/{len(tasks)}] {task.id}: {task.command.strip().splitlines()[0][:70]}")
            res, text = run_task(client, task, args)
            body = build_header(task, idx, len(tasks), args.user, args.host)
            body += sanitize_output(text, args.keep_cr)
            body += build_footer(res)

            ansi_path = out_dir / f"{task.safe_id}.ansi"
            ansi_path.write_text(body, encoding="utf-8")
            res.ansi_file = ansi_path.name

            if termshot:
                png_path = out_dir / f"{task.safe_id}.png"
                ok, err = render(termshot, ansi_path, png_path, args)
                if ok:
                    res.png_file = png_path.name
                else:
                    res.error = res.error or err
                    eprint(f"    [!] 出图失败: {err}")
            elif not args.no_render:
                res.error = res.error or "termshot 缺失"

            state = "ok" if res.ok else ("TIMEOUT" if res.timed_out else "FAIL")
            eprint(f"    -> {state}  exit={res.exit_status}  {res.duration:.2f}s  {res.output_bytes}B")
            results.append(res)
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
