# 故障排查

按「症状 → 原因 → 处理」组织。先看终端输出，再看 `manifest.json` 里对应条目的 `error` 字段。

## 连接阶段

**`[!] 无法连接到 <host>:<port>`**
网络不通、端口不对或防火墙拦截。先 `nc -vz <host> <port>` 验证；确认端口后用 `--port` 指定。

**`[!] 认证失败: 账号/密码/私钥不正确`**
密码错误、密钥未授权、或该账号仅允许特定认证方式。
用 `ssh -v <user>@<host>` 对比服务端授受的方法；必要时 `--no-agent` 避免 agent 抢先试错。

**`[!] 未提供密码且无法交互输入`**
在非 TTY 环境（cron、CI、脚本嵌套）里没有传 `-p`。显式传 `-p` 或用 `-i` 私钥。

**主机密钥告警 / `Host key verification failed`**
脚本使用 `AutoAddPolicy`，一般不会出现；若环境强制校验 `known_hosts`，先 `ssh-keyscan` 登记主机公钥。

## 执行阶段

**命令一直挂到超时（`TIMEOUT`，图上黄字）**
大概率是交互式程序在等输入：`less`、`more`、`vim`、全屏 `top`、`mysql`/`psql` 交互客户端、需要确认的 `apt`。
处理：改用非交互写法（`top -b -n1`、`| cat`、`-y`、`-N`），或加 `--no-pty`，必要时提高 `--timeout`。

**输出乱码 / 颜色码裸露成 `^[[0m` 之类**
远端 `TERM` 异常或输出含 termshot 无法解析的控制序列。
处理：显式 `--term xterm-256color`；仍异常时加 `--no-pty`（牺牲颜色换正确性），或命令里去掉 `--color=always`。

**图里行被截断、或大段空白**
`--render-columns` 同时固定了换行与画布宽度。去掉该参数让 termshot 自适应，或调到接近真实行宽。

**截图内容对不上命令**
`id` 重复导致文件互相覆盖。确认清单里的 `id` 唯一；未指定时脚本会自动编号 `rowNN`。

**退出码 127 / `command not found`**
命令在远端不存在，或 `--remote-script` 指向的解释器缺失。先跑 `command -v <bin>` 确认；必要时用 `--shell` 指定绝对路径解释器（如 `/usr/bin/python3`）。

## 脚本相关

**`[!] 远端脚本不存在或不可访问`**
`--remote-script` 路径写错，或账号无读权限。用 `-c "ls -l <path>"` 核对；必要时改用 `--script` 上传自己的副本。

**`[!] 脚本准备失败: size mismatch in put!`**
SFTP 写入被中断（磁盘满、配额、目标目录不可写、跳板机限制）。换 `--remote-dir` 到可写目录，并确认剩余空间。

**上传成功但执行报 `Permission denied`**
远端 `/tmp` 挂载了 `noexec`。改用可执行目录（如 `--remote-dir ~/tmp`），或在命令里显式用解释器执行（默认即是 `bash <path>` 形式，可绕开 exec 挂载限制）。

**远端残留了上传的脚本**
默认采集结束会删除；使用了 `--keep-remote`、或命令超时导致清理流程被打断时会保留。
手动清理：`rm -rf /tmp/ssh_shot_<时间戳>`。

## 渲染阶段

**`[!] 未找到 termshot`（退出码 2）**
未安装或不在查找路径内。安装：

```bash
go install github.com/homeport/termshot/cmd/termshot@latest   # 产物在 $(go env GOPATH)/bin
brew install homeport/tap/termshot
```

或用 `--termshot /path/to/termshot` 指定。也可先 `--no-render` 只留 `.ansi`，事后补出图。

**`go install` 卡在 `sum.golang.org` 超时**
校验服务不可达。改用 `GOSUMDB=off go install ...`，或直接下载 Release 二进制放入 `PATH`。

**`termshot 退出码 1: failed to read contents`**
`.ansi` 文件为空或路径不可读。检查该条命令是否真的产生了输出（部分命令只在 stderr 输出，PTY 模式下会合并，`--no-pty` 模式会标注 `[stderr]`）。

**图片生成但很小 / 空白**
输出全是控制字符（如 `clear`、进度条刷新）。去掉 `clear`，或换用一次性输出的命令。

## 其他

**采集过程中 Ctrl-C**
已生成的截图与 `.ansi` 会保留，但 `manifest.json` 不会写入（在退出码 `130` 时退出）。若需完整清单，重跑一次。

**目标主机窗口很窄、输出被远端截断**
远端依据 PTY 列宽换行。加大 `--cols`（默认 200）；某些程序需同时设置 `COLUMNS=<n>`，可在命令前加。
