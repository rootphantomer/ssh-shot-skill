# CLI 参考

`scripts/ssh_shot.py` 的完整参数、退出码与输出字段。

## 参数

### 连接

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` | 必填 | 目标主机 IP / 域名 |
| `--port` | `22` | SSH 端口 |
| `-u, --user` | `root` | 登录用户名 |
| `-p, --password` | 交互输入 | 密码；省略则 `getpass` 提示 |
| `-i, --key` | – | 私钥路径 |
| `--key-passphrase` | – | 私钥口令 |
| `--ask-key-passphrase` | 关 | 使用私钥时交互索要口令 |
| `--no-agent` | 关 | 不使用 ssh-agent |
| `--connect-timeout` | `15` | 连接超时（秒） |

### 执行源

| 参数 | 说明 |
| --- | --- |
| `-c, --command` | 要执行的命令，可重复 |
| `--commands-json` | 命令清单 JSON 文件 |
| `--script` | 本地脚本：SFTP 上传后执行，可重复 |
| `--remote-script` | 远端已有脚本，可重复（不修改远端文件） |
| `--shell` | 解释器，如 `bash`、`python3`、`"bash -e"`；缺省按扩展名 / shebang 推导 |
| `--remote-dir` | 上传目录，默认 `/tmp/ssh_shot_<时间戳>` |
| `--keep-remote` | 保留远端上传的脚本与目录 |
| `--no-fetch-script` | 不把远端脚本拉回证据目录存证 |
| `--timeout` | 单条命令超时（秒），默认 `60` |

解释器推导顺序：`--shell` → 扩展名（`.sh`/`.bash`→bash、`.zsh`、`.ksh`、`.py`→python3、`.pl`→perl、`.rb`→ruby）→ shebang（支持 `#!/usr/bin/env python3`、`#!/bin/bash -e`）→ 兜底 `bash`。所有脚本路径经 `shlex.quote` 转义。

### 终端与会话

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--cols` | `200` | 远端 PTY 列宽 |
| `--rows` | `60` | 远端 PTY 行高 |
| `--term` | `xterm-256color` | PTY `TERM` 类型 |
| `--no-pty` | 关 | 不申请 PTY：无颜色，但不会被分页器卡住 |
| `--keep-cr` | 关 | 保留 `\r`（默认转换为 `\n`） |

### 输出与渲染

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-o, --output` | `./evidence` | 证据根目录 |
| `--termshot` | 自动查找 | termshot 可执行文件路径 |
| `--no-render` | 关 | 只保存 `.ansi`，不出图 |
| `--render-columns` | `0` | 出图时强制换行列数（`0` 不强制；会固定画布宽度） |
| `--margin` | – | 图片外边距（像素） |
| `--padding` | – | 图片内边距（像素） |
| `--no-shadow` | 关 | 不画窗口阴影 |
| `--no-decoration` | 关 | 不画窗口标题栏按钮 |
| `--render-timeout` | `90` | 单张渲染超时（秒） |

termshot 自动查找顺序：`PATH` → `~/go/bin` → `/usr/local/bin` → `/opt/homebrew/bin` → `/usr/bin`。

## 命令清单格式

```json
[
  {
    "id": "row01",
    "name": "basic-info",
    "command": "hostname; uname -a; id",
    "timeout": 30
  }
]
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 否 | 截图文件名前缀，缺省自动编号 `rowNN`；会做文件名安全化 |
| `name` | 否 | 描述性标签，写入清单与截图标题 |
| `command` | 是 | 实际下发的命令 |
| `timeout` | 否 | 覆盖全局 `--timeout` |

也接受 `{"commands": [...]}` 包裹形式，或纯字符串数组。

## 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 全部采集成功 |
| `1` | 有命令失败或超时（截图仍生成，图上红/黄标注） |
| `2` | 环境或参数问题：termshot 缺失、脚本不存在、无命令可执行 |
| `130` | 用户中断（Ctrl-C） |

## manifest.json

```json
{
  "tool": "ssh_shot.py",
  "created_at": "2026-11-18T11:08:52",
  "target": { "host": "10.0.0.5", "port": 22, "user": "root" },
  "render": { "termshot": "/usr/local/bin/termshot", "columns": null, "pty": true, "cols": 200, "rows": 60 },
  "scripts": {
    "remote_dir": "/tmp/ssh_shot_20261118-110852",
    "kept": false,
    "uploaded": ["/tmp/ssh_shot_20261118-110852/baseline.sh"],
    "shell_override": null
  },
  "summary": { "total": 2, "ok": 2, "failed": 0 },
  "results": [
    {
      "id": "row01",
      "name": "basic-info",
      "command": "uname -a",
      "exit_status": 0,
      "duration": 0.04,
      "output_bytes": 531,
      "ansi_file": "row01.ansi",
      "png_file": "row01.png",
      "error": "",
      "timed_out": false,
      "local_script": "",
      "remote_script": "",
      "interpreter": "",
      "sha256": "",
      "evidence_script": ""
    }
  ]
}
```

字段说明：

- `render.columns` —— 出图列宽，`null` 表示不强制。
- `scripts` —— 仅在使用了脚本时才有内容；`uploaded` 是本程序上传过的远端路径。
- `results[].duration` —— 秒，浮点。
- `results[].error` —— 非空表示该条失败（超时、连接中断、渲染失败等）。
- `results[].sha256` —— 脚本内容哈希；纯命令任务为空。
- `results[].evidence_script` —— 证据目录中的脚本副本文件名。

## 渲染样式

```bash
# 更宽的画布、无阴影、无标题栏按钮
./.venv/bin/python scripts/ssh_shot.py --host H -p P -c "ss -lntp" \
    --render-columns 160 --no-shadow --no-decoration --margin 24 --padding 12
```

`--render-columns` 会同时约束换行与画布宽度：内容较窄时画面两侧留白较多，日常建议不指定，让 termshot 自适应。
