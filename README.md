# ssh-shot — SSH 命令执行 + 输出截图取证

> 这是一个 Agent Skill（见 [SKILL.md](SKILL.md)）：把 `SKILL.md` 放进你的技能目录（如 `~/.pi/agent/skills/ssh-shot/`），
> Agent 在遇到「SSH 跑命令/脚本 + 截图取证」类任务时会自动使用。也可以直接当命令行工具用。

给一组 SSH 凭据和命令，逐条在远端执行，把每条命令的完整输出（含 ANSI 颜色）交给
[termshot](https://github.com/homeport/termshot) 渲染成 PNG 截图，并生成 `manifest.json` 归档。

用于：系统巡检、故障排查、安全基线核查、应急响应验证、审计取证。

## 安装

```bash
git clone https://github.com/rootphantomer/ssh-shot-skill.git && cd ssh-shot-skill
bash scripts/install.sh
```

`install.sh` 会建 `.venv` 装 paramiko，再装 termshot（有 go 就 `go install`，没有则提示用 brew 或下 release）。
手动装也可以：

```bash
uv venv .venv && uv pip install --python .venv/bin/python paramiko
go install github.com/homeport/termshot/cmd/termshot@latest   # 产物在 ~/go/bin/termshot
# 或 brew install homeport/tap/termshot
```

> 脚本会自动在 `PATH`、`~/go/bin`、`/usr/local/bin`、`/opt/homebrew/bin`、`/usr/bin`
> 里找 termshot，也可以用 `--termshot /path/to/termshot` 指定。

## 用法

```bash
# 单条命令（密码直传；不传 -p 会交互提示）
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 -p 'pass' -c "uname -a"

# 多条命令
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 --user root -p 'pass' \
    -c "hostname; uptime" -c "ss -lntp"

# 命令清单文件（推荐批量巡检）
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 -p 'pass' \
    --commands-json scripts/commands.sample.json -o ./evidence

# 本地脚本（上传后执行，默认执行完删远端临时文件）
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 -p 'pass' --script ./check.sh

# 远端已有脚本（直接执行）
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 -p 'pass' --remote-script /opt/audit/check.sh

# 脚本 + 命令混合，一次采集多张图
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 -p 'pass' \
    --script ./baseline.sh -c "ss -lntp" --remote-script /opt/audit/os.sh

# 私钥登录 + 非默认端口 + 窄终端
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 --port 2222 -u ops \
    -i ~/.ssh/id_ed25519 -c "systemctl status sshd" --cols 160

# 只要文本不要图片
./.venv/bin/python scripts/ssh_shot.py --host 10.0.0.5 -p 'pass' -c "id" --no-render
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | 必填 | 目标主机 |
| `--port` | 22 | SSH 端口 |
| `--user / -u` | root | 用户名 |
| `--password / -p` | 交互 | 密码；不传则 `getpass` 提示 |
| `--key / -i` | – | 私钥路径 |
| `--key-passphrase` | – | 私钥口令（或 `--ask-key-passphrase` 交互） |
| `-c / --command` | – | 命令，可重复 |
| `--commands-json` | – | 命令清单 JSON |
| `--script` | – | 本地脚本：SFTP 上传后执行，可重复 |
| `--remote-script` | – | 远端已有脚本路径，可重复 |
| `--shell` | 自动推导 | 解释器，如 `bash`、`python3`、`"bash -e"` |
| `--remote-dir` | `/tmp/ssh_shot_<时间戳>` | 上传脚本的远端目录 |
| `--keep-remote` | 关 | 保留远端上传的脚本与目录 |
| `--no-fetch-script` | 关 | 不把远端脚本拉回证据目录 |
| `-o / --output` | `./evidence` | 证据根目录 |
| `--cols` / `--rows` | 200 / 60 | 远端 PTY 尺寸 |
| `--term` | xterm-256color | PTY TERM |
| `--no-pty` | 关 | 不申请 PTY（无颜色，但不会被 less/invoke pager 卡住） |
| `--timeout` | 60 | 单命令超时秒 |
| `--termshot` | 自动查找 | termshot 路径 |
| `--no-render` | 关 | 只保存 `.ansi` |
| `--render-columns` | 0 | 出图强制换行列数 |
| `--margin` / `--padding` | – | 图片外边距 / 内边距 |
| `--no-shadow` / `--no-decoration` | 关 | 去掉窗口阴影 / 标题栏按钮 |

## 执行脚本

两种模式，可以混用，也可以和 `-c` 混用：

**A. 本地脚本（`--script ./check.sh`）**

1. SFTP 上传到 `--remote-dir`（默认 `/tmp/ssh_shot_<时间戳>/`），`chmod 700`
2. 执行 `bash /tmp/.../check.sh`（解释器见下）
3. 截图
4. 默认删除远端上传的脚本和目录（`--keep-remote` 保留）

脚本原件同时复制进证据目录（`script.row01.check.sh`）并记录 sha256，截图顶部也会带上
`# uploaded: <本地路径> sha256: …`，用来证明「跑的是哪一份脚本」。

**B. 远端已有脚本（`--remote-script /opt/audit/check.sh`）**

1. 先 `stat` 确认存在，不存在直接报错退出（不会静默跑出一个 exit 127）
2. 读首行 shebang 推导解释器
3. 把远端脚本内容拉回证据目录（`script.row02.check.sh`）+ 记 sha256（`--no-fetch-script` 关闭）
4. 执行、截图，**不改动远端任何文件**

**解释器推导顺序**：`--shell` 显式指定 → 文件扩展名（`.sh`→bash、`.py`→python3、`.pl`→perl…）
→ shebang（`#!/usr/bin/env python3` → `python3`，支持 `#!/bin/bash -e` 这种带参数的）→ 兜底 `bash`。

任何脚本路径都经过 `shlex.quote` 转义，带空格/中文的路径也安全。

## 产物

```
evidence/<host>_<YYYYmmdd-HHMMSS>/
  manifest.json      # 主机、时间、渲染参数、脚本信息、每条命令的退出码/耗时/文件
  script.row01.xxx.sh  # 脚本存证副本（sha256 记在 manifest 中）
  row01.ansi         # 捕获到的原始 ANSI 文本（取证留底，可重出图）
  row01.png          # 截图
  ...
```

重出图：`termshot --raw-read row01.ansi --filename row01.png`。

每张截图的版式自带溯源信息：`[row05] 名称 (5/5)` + 彩色提示符行（命令原文）+ 输出 + `[exit 0] (0.12s)`。

## 命令清单格式

```json
[
  { "id": "row01", "name": "basic-info", "command": "hostname; uname -a", "timeout": 30 }
]
```

`id` 决定文件名（会做安全化处理），`name` 只进 manifest 与截图标题，`timeout` 可覆盖全局超时。

## 退出码

- `0` 全部采集成功
- `1` 有命令失败或超时（截图仍会生成，`[exit N]` / `[timeout]` 会画在图上）
- `2` 参数/环境错误（如 termshot 缺失）

## 已知限制

- 需要交互的程序（`less`、`vim`、`top` 全屏刷新）在截图里会失真；建议用 `--no-pty` 或
  `| cat`、`top -b -n1` 这类非交互写法。
- 超宽单行会被 termshot 按 `--render-columns` 或自动宽度截断/换行，必要时调 `--render-columns`。
- 远端若强制 TMOUT、登录 banner 很长，会出现在输出顶部（属真实情况，不做过滤）。
- 只读优先：脚本不修改远端任何内容，命令内容完全由你决定。

## 自测

仓库内含一个最小 SSH 服务端，可在没有真实远端的情况下端到端验证：

```bash
.venv/bin/python scripts/fake_sshd.py --port 2222 --password 123456 &
.venv/bin/python scripts/ssh_shot.py --host 127.0.0.1 --port 2222 -p 123456 \
    -c "uname -a; id" -c "echo $((1+1))" -o /tmp/evidence
```
