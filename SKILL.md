---
name: ssh-shot
description: 通过 SSH 在远端 Linux/Unix 主机执行命令或脚本，并用 termshot 把每条命令的完整输出（含 ANSI 颜色）渲染成 PNG 截图，同时生成 manifest.json 归档。当任务涉及服务器巡检、故障排查、安全基线核查、应急响应验证、审计取证、需要"命令输出截图证明"，或要求 SSH 执行命令/上传脚本并留痕时使用。Use whenever a Linux/Unix task needs SSH command execution with screenshot evidence, e.g. baseline checks, troubleshooting, incident-response verification, or audit evidence collection. Not for Excel/report editing alone.
version: 1.0
author: rootphantomer
license: MIT
---

# SSH Shot — SSH 命令执行 + 输出截图取证

把「连上去跑命令」和「截图留证」合成一步：给凭据和目标，自动执行命令清单 / 本地脚本 / 远端脚本，
每条输出渲染成一张终端风格 PNG，并把命令、退出码、耗时、脚本 sha256 记入 `manifest.json`。

## 什么时候用

- 服务器巡检、基线核查、安全加固前后对比
- 故障排查，需要留下"我确实跑过这条命令、结果是这样"的证据
- 应急响应：抓取现场状态（进程、端口、日志、账号）并归档
- 审计取证：需要可复核的截图 + 清单，而不是一段随手复制的文本

不适用：只是编辑 Excel/报表；只需要拿到命令输出文本（不需要图）时可直接用 ssh。

## 前置条件

- 本机 macOS/Linux（Windows 需在 WSL 或 Git Bash 下用）
- `paramiko`（Python）
- `termshot`（Go CLI，负责 ANSI→PNG）

一键装依赖：

```bash
bash scripts/install.sh
```

手动装：

```bash
uv venv .venv && uv pip install --python .venv/bin/python paramiko
go install github.com/homeport/termshot/cmd/termshot@latest   # 产物 ~/go/bin/termshot
```

脚本会自动在 `PATH`、`~/go/bin`、`/usr/local/bin`、`/opt/homebrew/bin`、`/usr/bin` 找 termshot，
也可用 `--termshot /path/to/termshot` 指定。

## 用法

```bash
PY=.venv/bin/python     # 或直接 python3（已装 paramiko）

# 1) 单条/多条命令
$PY scripts/ssh_shot.py --host 10.0.0.5 -p 'PASS' -c "uname -a; id" -c "ss -lntp"

# 2) 命令清单（推荐批量巡检，格式见 scripts/commands.sample.json）
$PY scripts/ssh_shot.py --host 10.0.0.5 -p 'PASS' \
    --commands-json scripts/commands.sample.json -o ./evidence

# 3) 本地脚本：上传 → 执行 → 截图 → 默认删远端临时文件
$PY scripts/ssh_shot.py --host 10.0.0.5 -p 'PASS' --script ./baseline.sh

# 4) 远端已有脚本：直接执行（不改动远端任何文件）
$PY scripts/ssh_shot.py --host 10.0.0.5 -p 'PASS' --remote-script /opt/audit/os.sh

# 5) 私钥 + 非默认端口 + 窄终端
$PY scripts/ssh_shot.py --host 10.0.0.5 --port 2222 -u ops -i ~/.ssh/id_ed25519 \
    -c "systemctl status sshd" --cols 160

# 6) 只要文本不要图
$PY scripts/ssh_shot.py --host 10.0.0.5 -p 'PASS' -c "id" --no-render
```

密码 `-p` 直传；不传则 `getpass` 交互提示（避免明文进 shell history）。

### 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | 必填 | 目标主机 |
| `--port` / `--user` | 22 / root | |
| `-p / --password`、`-i / --key` | 交互 | 认证 |
| `-c` / `--commands-json` | – | 命令（可重复）/ 命令清单 |
| `--script` / `--remote-script` | – | 本地脚本（上传）/ 远端脚本 |
| `--shell` | 自动 | 解释器，如 `bash`、`python3`、`"bash -e"` |
| `--remote-dir` / `--keep-remote` | `/tmp/ssh_shot_<ts>` / 关 | 上传目录 / 保留远端脚本 |
| `-o / --output` | `./evidence` | 证据根目录 |
| `--cols` / `--rows` / `--term` | 200 / 60 / xterm-256color | 远端 PTY |
| `--no-pty` | 关 | 不申请 PTY（无颜色，但不会被 pager 卡住） |
| `--timeout` | 60 | 单命令超时秒 |
| `--no-render` / `--render-columns` | 关 / 0 | 只存文本 / 出图列宽 |
| `--margin` `--padding` `--no-shadow` `--no-decoration` | – | 图片样式 |

## 产物

```
evidence/<host>_<YYYYmmdd-HHMMSS>/
  manifest.json              # 主机、时间、渲染参数、脚本信息、每条命令的退出码/耗时
  row01.ansi                 # 捕获的原始 ANSI 文本（可重出图）
  row01.png                  # 截图
  script.row02.baseline.sh   # 脚本存证副本（sha256 记在 manifest 里）
```

每张图自带溯源头：`[row05] 名称 (5/5)` + 彩色提示符行（命令原文）+ 输出 + `[exit 0] (0.12s)`；
脚本任务多一行 `# uploaded: /本地/路径 sha256: …`。超时为黄字，非零退出为红字。

重出图：`termshot --raw-read row01.ansi --filename row01.png`

退出码：`0` 全成功 / `1` 有命令失败或超时（图仍出） / `2` 环境问题（如 termshot 缺失）。

## 采集规范

- **只读优先**：默认只跑查询类命令；任何写操作先跟用户确认。
- 每条命令自描述，避免事后无法对应：`clear; echo 'ROW 05'; echo 'COMMAND: ...'; <cmd>; echo 'END ROW 05'`
  或直接把 `name` 写进 `--commands-json`。
- 长输出拆成多条（每张图信息量有限），超宽单行用 `--render-columns` 控制。
- 交互式程序（`less`/`vim`/`top`）会失真：用 `--no-pty` 或 `top -b -n1`、`| cat` 之类非交互写法。
- 敏感信息（口令、密钥、token）不要进命令，或事后用 `termshot --edit` 删。

## 自测

内置一个最小 SSH 服务端，无需真实远端即可端到端验证（仅监听 127.0.0.1）：

```bash
$PY scripts/fake_sshd.py --port 2222 --password 123456 &
$PY scripts/ssh_shot.py --host 127.0.0.1 --port 2222 -p 123456 \
    -c "uname -a; id" --script ./baseline.sh -o /tmp/evidence
```
