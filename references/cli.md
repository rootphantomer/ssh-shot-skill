# CLI 参考

`scripts/ssh_shot.py` 的完整参数、退出码与输出字段。

## 参数

### 连接

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` | – | 目标主机 IP / 域名（与 `--hosts-file` 二选一） |
| `--hosts-file` | – | 主机清单纯文本，每行 `ip user password`（`#` 注释）；批量对多台执行同一批命令 |
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
| `--width-source` | `screen` | 宽度基准：`screen`=本机最大屏幕物理像素（默认），`terminal`=当前终端列数 |
| `--merge` | 关 | 多命令按序执行、输出拼接成单张 `merged.<ext>`（不逐条出图）；图尾仅一个汇总 exit/error/timeout（最差状态 + 总耗时） |
| `--render-columns` | `0` | 手动指定列数（`0` = 由 `--width-source` 决定） |
| `--padding` | – | 图片内边距（像素） |
| `--no-decoration` | 关 | 不画窗口标题栏按钮（渲染固定 `--clip-canvas --no-shadow`，终端框铺满、无阴影） |
| `--render-timeout` | `90` | 单张渲染超时（秒） |
| `--image-format` | `jpeg` | 输出格式：`png` / `jpg` / `jpeg` / `webp` / `bmp`；`jpg` 与 `jpeg` 均存为 `.jpg` |
| `--image-quality` | `90` | 有损格式质量 1–100，对 jpg / jpeg / webp 生效 |
| `--image-background` | `auto` | 底色：`auto` / `black` / `white` / `#RRGGBB`；默认取截图自带深色，不铺白底（仅 jpg/jpeg/bmp 用到） |

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
| `1` | 有命令失败或超时（截图仍生成，图上红/黄标注），或批量模式有主机失败 |
| `2` | 环境或参数问题：termshot 缺失、脚本不存在、无命令可执行、缺 Pillow、`--image-quality` 越界 |
| `130` | 用户中断（Ctrl-C） |

## manifest.json

```json
{
  "tool": "ssh_shot.py",
  "created_at": "2026-11-18T11:08:52",
  "target": { "host": "10.0.0.5", "port": 22, "user": "root" },
  "render": { "termshot": "/usr/local/bin/termshot", "columns": null, "pty": true, "cols": 200, "rows": 60,
              "format": "jpeg", "quality": 90, "background": "auto",
              "clip_canvas": true, "shadow": false,
              "width_source": {"source": "screen", "screen_px": 3072, "columns": 103}, "merge": false },
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
      "image_file": "row01.jpg",
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

- `render.columns` —— 手动指定的列数，`null` 表示按输出自适应（每条实际列数见 `results[].columns`）。
- `render.format` / `render.quality` / `render.background` / `render.width` —— 输出格式、有损质量、底色、固定画布宽度（`null` 表示自适应）。
- `results[].image_file` —— 实际生成的截图文件名；后缀由 `render.format` 决定。
- `scripts` —— 仅在使用了脚本时才有内容；`uploaded` 是本程序上传过的远端路径。
- `results[].duration` —— 秒，浮点。
- `results[].error` —— 非空表示该条失败（超时、连接中断、渲染失败等）。
- `results[].sha256` —— 脚本内容哈希；纯命令任务为空。
- `results[].evidence_script` —— 证据目录中的脚本副本文件名。

## 渲染样式

```bash
# 手动固定列宽、无标题栏按钮（默认按输出自适应列宽、终端框铺满、无阴影）
./.venv/bin/python scripts/ssh_shot.py --host H -p P -c "ss -lntp" \
    --render-columns 160 --no-decoration --padding 12
```

默认取本机最大屏幕物理像素宽度（macOS `system_profiler` / Linux `xrandr`，拿不到则回退终端列数），渲染前用 20/40 列探针校准 px/列再换算列数；`--width-source terminal` 则直接用当前终端列数（`COLUMNS` 或非 TTY 回退）。两者都拿不到时按输出最长行自适应（45–120）。渲染固定 `--clip-canvas --no-shadow`：终端框铺满、无阴影、无背景块。需要严格控制列数时用 `--render-columns` 覆盖。

`--merge` 示例：多条命令依次执行后拼成一张 `merged.<ext>`，适合按顺序展示一段会话（管道符连接的仍是单条命令）。非 merge 模式每张图各自带本命令的 exit；merge 模式只在图尾显示一个汇总 footer：exit 取所有命令里最差值（任一非 0 即非 0），耗时为各命令之和，任一超时/失败则显示对应的 timeout/error（黄/红字）。各命令的单条 `.ansi` 仍保留自己的 exit。

### 批量模式

```bash
./.venv/bin/python scripts/ssh_shot.py --hosts-file hosts.txt \
    --commands-json cmds.json -o ./巡检报告
```

- 清单每行 `ip user password`，空行与 `#` 注释忽略；格式见 `scripts/hosts.sample.txt`。
- 每台主机用各自的账号密码登录（忽略 `-p` / `-i`），执行同一批命令。
- 每台强制 `--merge`，只在最终目录留一张图，命名为 `{ip}_{目录名}.<ext>`，目录名 = `-o` 文件夹名；目录不存在自动新建。
- 最终目录**只有图片**，无 `.ansi` / `manifest.json` / 时间戳子目录。
- 单台连接/认证/采集失败不影响其他台，末尾汇总成功/失败数；退出码 `0` 全部成功、`1` 有失败。

格式示例：`--image-format png` 出无损 PNG；`--image-format jpg --image-quality 85` 出更小的 JPEG；`--image-format webp` 保留透明；`--image-format bmp --image-background '#101010'` 指定底色。termshot 本身只能输出 PNG，程序会先渲染到临时 PNG 再转换，临时文件自动清理。
