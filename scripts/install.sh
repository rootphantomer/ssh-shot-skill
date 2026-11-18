#!/usr/bin/env bash
# ssh-shot 依赖安装: paramiko (Python) + termshot (Go CLI)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> 1/2 安装 Python 依赖 (paramiko + Pillow)"
if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  uv pip install --python .venv/bin/python 'paramiko>=3.0' 'Pillow>=10'
elif command -v python3 >/dev/null 2>&1; then
  python3 -m venv .venv
  ./.venv/bin/python -m pip install -U pip 'paramiko>=3.0' 'Pillow>=10'
else
  echo "[!] 找不到 uv 或 python3，请先安装 Python 3.9+" >&2
  exit 1
fi

echo "==> 2/2 安装 termshot"
if command -v termshot >/dev/null 2>&1; then
  echo "    termshot 已存在: $(command -v termshot)"
elif command -v go >/dev/null 2>&1; then
  GOSUMDB="${GOSUMDB:-off}" go install github.com/homeport/termshot/cmd/termshot@latest
  echo "    已安装到 $(go env GOPATH)/bin/termshot"
else
  echo "[!] 未找到 termshot 且没有 go；请执行:" >&2
  echo "    brew install homeport/tap/termshot   # macOS" >&2
  echo "    或下载 https://github.com/homeport/termshot/releases" >&2
fi

echo "==> 完成，试运行:"
echo "    ./.venv/bin/python scripts/ssh_shot.py --help"
