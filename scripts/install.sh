#!/usr/bin/env bash
# ssh-shot 依赖安装: paramiko (Python) + termshot-cjk (Go CLI, 内置中文字体)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> 1/2 安装 Python 依赖 (paramiko + Pillow)"
if command -v uv >/dev/null 2>&1; then
  if [[ ! -x .venv/bin/python ]]; then
    if [[ -d .venv ]]; then uv venv --allow-existing .venv; else uv venv .venv; fi
  fi
  uv pip install --python .venv/bin/python 'paramiko>=3.0' 'Pillow>=10'
elif command -v python3 >/dev/null 2>&1; then
  [[ -x .venv/bin/python ]] || python3 -m venv .venv
  ./.venv/bin/python -m pip install -U pip 'paramiko>=3.0' 'Pillow>=10'
else
  echo "[!] 找不到 uv 或 python3，请先安装 Python 3.9+" >&2
  exit 1
fi

echo "==> 2/2 编译带中文字体的 termshot-cjk"
if [[ -x .venv/bin/termshot-cjk ]]; then
  echo "    已存在: .venv/bin/termshot-cjk (跳过编译；需要重建时删除该文件)"
else
  bash scripts/build_termshot.sh
fi

echo "==> 完成，试运行:"
echo "    ./.venv/bin/python scripts/ssh_shot.py --help"
