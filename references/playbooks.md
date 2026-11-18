# 巡检剧本

按主题分组、**全部只读**的命令清单，可直接复制成 `--commands-json` 文件。跨发行版的命令都做了 `||` 兜底；执行前按目标系统删掉不适用的分支即可，避免出现多条 `command not found`。

## 基础信息

```json
[
  { "id": "row01", "name": "os-identity", "command": "hostname; uname -a; cat /etc/os-release 2>/dev/null || cat /etc/redhat-release 2>/dev/null" },
  { "id": "row02", "name": "uptime-load", "command": "uptime; nproc 2>/dev/null || sysctl -n hw.ncpu" },
  { "id": "row03", "name": "memory", "command": "free -m 2>/dev/null || vm_stat" },
  { "id": "row04", "name": "disk-usage", "command": "df -h; echo; df -i" },
  { "id": "row05", "name": "time-zone", "command": "date; timedatectl 2>/dev/null || systemsetup -gettimezone 2>/dev/null" }
]
```

## 账号与认证

```json
[
  { "id": "row10", "name": "uid-zero", "command": "awk -F: '$3==0 {print}' /etc/passwd" },
  { "id": "row11", "name": "passwd-tail", "command": "tail -n 15 /etc/passwd" },
  { "id": "row12", "name": "shadow-perm", "command": "ls -l /etc/shadow /etc/gshadow 2>/dev/null" },
  { "id": "row13", "name": "sudoers", "command": "grep -rEv '^\\s*#|^\\s*$' /etc/sudoers /etc/sudoers.d/ 2>/dev/null | head -40" },
  { "id": "row14", "name": "sudo-group", "command": "getent group sudo wheel 2>/dev/null" },
  { "id": "row15", "name": "recent-login", "command": "last -n 15 2>/dev/null; echo; lastb -n 10 2>/dev/null || echo 'no lastb'" }
]
```

## 网络与端口

```json
[
  { "id": "row20", "name": "listening", "command": "ss -lntup 2>/dev/null || netstat -lntup 2>/dev/null" },
  { "id": "row21", "name": "established", "command": "ss -tn state established 2>/dev/null | head -30 || netstat -tn 2>/dev/null | head -30" },
  { "id": "row22", "name": "routes", "command": "ip route 2>/dev/null || netstat -rn | head -20" },
  { "id": "row23", "name": "interfaces", "command": "ip -br a 2>/dev/null || ifconfig | grep -E '^[a-z]|inet '" },
  { "id": "row24", "name": "resolv", "command": "cat /etc/resolv.conf 2>/dev/null; echo; cat /etc/hosts" },
  { "id": "row25", "name": "firewall", "command": "iptables -S 2>/dev/null | head -40 || nft list ruleset 2>/dev/null | head -40 || firewall-cmd --list-all 2>/dev/null || echo 'no firewall cli'" }
]
```

## 服务与进程

```json
[
  { "id": "row30", "name": "failed-units", "command": "systemctl --failed --no-pager 2>/dev/null || echo 'no systemd'" },
  { "id": "row31", "name": "ssh-service", "command": "systemctl status sshd ssh --no-pager 2>/dev/null | head -30 || service ssh status 2>/dev/null" },
  { "id": "row32", "name": "top-cpu", "command": "ps -eo pid,ppid,user,pcpu,pmem,etime,args --sort=-pcpu 2>/dev/null | head -15 || ps aux | head -15" },
  { "id": "row33", "name": "cron", "command": "crontab -l 2>/dev/null; echo '--- /etc/cron.d'; ls -l /etc/cron.d 2>/dev/null; echo '--- systemd timers'; systemctl list-timers --no-pager 2>/dev/null | head -15" },
  { "id": "row34", "name": "startup-items", "command": "systemctl list-unit-files --state=enabled --no-pager 2>/dev/null | head -30 || ls /etc/init.d 2>/dev/null" }
]
```

## 日志

```json
[
  { "id": "row40", "name": "auth-log", "command": "tail -n 40 /var/log/auth.log 2>/dev/null || tail -n 40 /var/log/secure 2>/dev/null || echo 'no auth log'" },
  { "id": "row41", "name": "ssh-failures", "command": "grep -c 'Failed password' /var/log/auth.log /var/log/secure 2>/dev/null; grep 'Failed password' /var/log/auth.log /var/log/secure 2>/dev/null | tail -10" },
  { "id": "row42", "name": "syslog-errors", "command": "grep -iE 'error|fail|panic' /var/log/syslog /var/log/messages 2>/dev/null | tail -25" },
  { "id": "row43", "name": "journal-errors", "command": "journalctl -p err -n 30 --no-pager 2>/dev/null || echo 'no journald'" }
]
```

## 补丁与软件

```json
[
  { "id": "row50", "name": "kernel", "command": "uname -r; echo; ls /boot 2>/dev/null | head -10" },
  { "id": "row51", "name": "pending-updates", "command": "apt list --upgradable 2>/dev/null | head -20 || dnf check-update 2>/dev/null | head -20 || yum check-update 2>/dev/null | head -20 || echo 'unknown package manager'" },
  { "id": "row52", "name": "installed-count", "command": "dpkg -l 2>/dev/null | grep -c '^ii' || rpm -qa 2>/dev/null | wc -l" },
  { "id": "row53", "name": "suid-files", "command": "find / -xdev -perm -4000 -type f 2>/dev/null | head -25" }
]
```

## 使用建议

```bash
./.venv/bin/python scripts/ssh_shot.py --host <HOST> -u <USER> -p '<PASS>' \
    --commands-json ./row-basic.json -o ./evidence
```

- 按主题分批执行，证据目录按时间戳区分，便于对照加固前后。
- 单条命令输出过长（如全量 `dpkg -l`、`ss -a`）时只取头尾，或拆成多条分别截图。
- `find /` 类命令在大文件系统上耗时长，按需提高该条的 `timeout`，或限定目录范围。
- 涉及敏感内容的输出（`/etc/shadow` 内容、密钥文件正文）不要执行；用权限、哈希、行数等元信息替代。
