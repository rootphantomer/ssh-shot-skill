---
name: ssh-shot
description: "通过 SSH 在远端 Linux/Unix 主机上执行命令或脚本，并把每条命令的完整输出（含 ANSI 颜色）用 termshot 渲染成终端风格截图（png / jpg / jpeg / webp / bmp，默认 jpeg），同时生成 manifest.json 归档（命令原文、退出码、耗时、脚本 SHA-256）。适用于服务器巡检、安全基线核查、故障排查、应急响应验证、审计取证，以及任何需要「命令输出截图」作为证据的场合。Use when a task needs SSH command or script execution on a remote Linux/Unix host together with screenshot evidence: system inspection, security baseline checks, troubleshooting, incident-response verification, audit evidence collection, or when the user asks for command-output screenshots."
license: MIT
compatibility: 需要本机 Python 3.9+、paramiko、Pillow 10+（出图必需，或使用 scripts/install.sh 自动安装）、termshot CLI（Go，0.4+），以及到目标主机的 SSH 网络连通性与可用凭据。不依赖远端安装任何软件；Windows 需在 WSL 或 Git Bash 下运行。
metadata:
  author: rootphantomer
  version: "2.0.0"
  repository: https://github.com/rootphantomer/ssh-shot-skill
---

# SSH Shot

## Overview

将「SSH 执行命令」与「截图留证」合并为一次操作：连上目标主机，逐条执行命令或脚本，把每条输出渲染成终端风格图片（png / jpg / jpeg / webp / bmp，默认 jpeg），并输出一份可编程复核的 `manifest.json`。

采集阶段不依赖本地伪终端：命令输出经 SSH 通道原样取回并落盘为 ANSI 文本，再由 termshot 按 `--width-source` 决定的列宽（默认本机屏幕物理像素，可切当前终端列数）离屏渲染成临时 PNG（终端框铺满、无阴影），最后按 `--image-format` 转成目标格式。因此渲染可离线重做，也不会因本地终端尺寸影响远端输出。

## When to use

在以下场景优先使用本技能，而不是手工执行命令再复制文本：

- 服务器巡检、安全基线核查、加固前后对比
- 故障排查，需要留下「确实执行过该命令、结果如此」的凭据
- 应急响应：抓取进程、端口、账号、日志现场并归档
- 审计与合规取证：交付物需要截图 + 机器可读清单
- 需要在多台主机上执行同一组命令并统一归档

不要使用本技能的情形：

- 只需要命令输出文本、不需要图片 → 直接 `ssh` 执行
- 编辑 Excel / Word / 报表 → 使用对应的文档技能
- 目标主机不可达或没有可用凭据 → 先解决连通性与认证

## Prerequisites

```bash
cd ssh-shot            # 目录名须与 name 字段一致
bash scripts/install.sh
```

`install.sh` 创建 `.venv` 安装 paramiko 与 Pillow，并安装 termshot（优先 `go install`，否则提示 brew / Release）。后续命令统一用 `.venv/bin/python`（或任意已装 paramiko / Pillow 的解释器）。

自检：`./.venv/bin/python scripts/ssh_shot.py --help` 能打印用法即就绪。

## Workflow

1. **确认边界**：主机、账号、认证方式（密码 / 私钥 / agent），以及是否允许写操作。默认只跑只读命令；任何变更类命令（重启服务、改权限、清日志、删文件）必须先取得用户明确授权。
2. **选择执行源**（可混用，按出现顺序各出一张图）：

   | 场景 | 参数 |
   | --- | --- |
   | 少量临时命令 | `-c "cmd"`（可重复） |
   | 批量巡检、需要稳定编号与备注 | `--commands-json cmds.json` |
   | 本机写好的脚本，要跑在远端 | `--script ./check.sh` |
   | 目标机上已有脚本 | `--remote-script /opt/audit/os.sh` |
   | 批量服务器（每台一张图） | `--hosts-file hosts.txt`（每行 `ip user password`） |

3. **执行**：标准调用形态如下，密码省略时脚本会 `getpass` 交互索要（避免明文进入 shell history）。多条命令想拼成一张图时加 `--merge`。

   批量服务器用 `--hosts-file`：对每台主机执行同一批命令，输出目录只有图片，命名为 `{ip}_{目录名}`（目录名 = `-o` 文件夹名），目录不存在自动新建；单台失败不影响其他台。

   ```bash
   ./.venv/bin/python scripts/ssh_shot.py \
       --host <HOST> -u <USER> -p '<PASS>' \
       -c "uname -a; uptime" \
       --commands-json scripts/commands.sample.json \
       -o ./evidence
   ```

4. **核对结果**：观察每条命令的 `exit=`、耗时与字节数；失败或超时会在终端打印 `TIMEOUT` / `FAIL`，并写入清单。
5. **汇报证据**：向用户给出证据目录、成功/失败计数，以及失败项的原因。截图内已自带命令原文与退出码，无需额外说明格式。

## Evidence layout

```text
evidence/<host>_<YYYYmmdd-HHMMSS>/
  manifest.json              # 目标、渲染参数（含 format/quality/background）、脚本信息、逐条结果
  row01.ansi / row01.jpg     # 原始 ANSI 文本 + 截图（后缀随 --image-format 变化）
  script.row02.baseline.sh   # 脚本存证副本（SHA-256 记在清单）

批量模式 (--hosts-file) 的输出目录只有图片:
  巡检报告/10.0.0.5_巡检报告.jpg, 10.0.0.6_巡检报告.jpg ...
```

每张图自带溯源信息：彩色提示符行（命令原文）、`[exit 0] (0.03s)`；脚本任务额外一行 `# uploaded: <路径> sha256: …`。超时黄字，非零退出红字。

重新渲染某一页（无需再次连接）：

```bash
# termshot 只能出 PNG；非 PNG 格式须再按 manifest.render.background 铺底转换
termshot --raw-read evidence/<dir>/row01.ansi --filename /tmp/row01.png
```

退出码：`0` 全部成功，`1` 有命令失败或超时（图仍生成），`2` 环境或参数问题。

## Rules

- **只读优先**。默认采集类命令；写操作需用户显式授权，并在汇报中说明改了什么。
- **命令可自解释**。批量采集时给每条命令写 `name`，或使用
  `clear; echo 'ROW 05'; echo 'COMMAND: ...'; <cmd>; echo 'END ROW 05'`，避免事后无法对应。
- **不落凭据**。密码只存在于进程内存；优先 `-i` 私钥或交互输入，不要在命令行参数里留下口令，也不要写进清单或截图。
- **敏感输出先裁剪**。结果可能含口令、密钥、令牌时，先调整命令（如 `| sed 's/password=.*/password=***/'`），或对 `.ansi` 用 `termshot --edit` 后再出图。
- **远端零残留**。`--script` 上传的脚本执行后默认删除；仅在排障需要时使用 `--keep-remote`，并在汇报中说明残留位置。
- **长输出拆分**。单张图信息量有限：多行输出拆成多条命令；超宽行由 termshot 折行，必要时用 `--render-columns` 手动指定列数。
- **交互式程序会失真**。`less` / `vim` / 全屏 `top` 请改用 `--no-pty`，或 `top -b -n1`、`| cat` 等非交互写法。

## Verification checklist

采集结束前逐项确认：

- [ ] 每条命令都有对应的截图（后缀与 `manifest.render.format` 一致），且图片内容与预期命令一致
- [ ] `manifest.json` 的 `summary.failed` 为 `0`，或失败项已查明原因并在汇报中说明
- [ ] 脚本任务的 `sha256` 与运行前记录的哈希一致
- [ ] 无口令、密钥、令牌出现在截图或清单中
- [ ] 使用 `--script` 时，远端临时文件已清理（或已说明保留位置）

## References

- [`references/cli.md`](references/cli.md) —— 完整参数表、退出码、`manifest.json` 字段、渲染样式开关
- [`references/troubleshooting.md`](references/troubleshooting.md) —— 连接失败、字符错乱、分页卡死、SFTP 报错等常见故障
- [`references/playbooks.md`](references/playbooks.md) —— 基础信息、账号、网络、服务、日志等只读巡检命令组
- [`scripts/commands.sample.json`](scripts/commands.sample.json) —— 命令清单格式样例
- [`README.md`](README.md) —— 面向人的完整文档
