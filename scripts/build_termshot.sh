#!/usr/bin/env bash
# 编译内置中文字体的 termshot-cjk
#
# 做法: 取上游 termshot 源码, 用 go.mod replace 把 github.com/gonvenience/font
# 换成 scripts/font-patch/ 里的实现(内嵌裁剪版 Sarasa Mono SC), 不改 Go 模块缓存。
# 产物默认写到 .venv/bin/termshot-cjk, ssh_shot.py 会优先使用它。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${TERMSHOT_VERSION:-v0.6.1}"
OUT="${TERMSHOT_CJK_BIN:-$ROOT/.venv/bin/termshot-cjk}"
FONT="$ROOT/assets/fonts/SSHShotMonoSC-GB2312.ttf"
PATCH_DIR="$ROOT/scripts/font-patch"

die() { echo "[!] $*" >&2; exit 1; }

command -v go >/dev/null 2>&1 || die "需要 Go 1.24+ 来编译 termshot-cjk；请先安装 Go 或手动准备该二进制"
[[ -f "$FONT" ]] || die "缺少字体文件: $FONT"
[[ -f "$PATCH_DIR/font-hack.go" ]] || die "缺少字体补丁: $PATCH_DIR/font-hack.go"

echo "==> 1/3 下载/确认 termshot $VERSION 源码"
if ! go mod download "github.com/homeport/termshot@$VERSION"; then
  echo "    默认 GOPROXY 不可用, 尝试 goproxy.cn ..."
  if GOPROXY='https://goproxy.cn,direct' GOSUMDB='sum.golang.google.cn' \
      go mod download "github.com/homeport/termshot@$VERSION"; then
    export GOPROXY='https://goproxy.cn,direct' GOSUMDB='sum.golang.google.cn'
  else
    echo "    网络下载失败, 尝试仅用本地模块缓存 ..."
    export GOPROXY=off GOSUMDB=off
    go mod download "github.com/homeport/termshot@$VERSION" || die "无法获取 termshot $VERSION 源码"
  fi
fi
src="$(go env GOMODCACHE)/github.com/homeport/termshot@$VERSION"
[[ -d "$src" ]] || die "未找到 termshot 源码目录: $src"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/ssh-shot-termshot.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
work="$tmp/termshot"
cp -R "$src" "$work"
chmod -R u+w "$work"

echo "==> 2/3 替换字体模块(内嵌 $(basename "$FONT"))"
fontmod="$tmp/font"
mkdir -p "$fontmod"
cp "$PATCH_DIR/go.mod" "$PATCH_DIR/font.go" "$PATCH_DIR/font-hack.go" "$fontmod/"
cp "$FONT" "$fontmod/SSHShotMonoSC-GB2312.ttf"
printf '\nreplace github.com/gonvenience/font => "%s"\n' "$fontmod" >> "$work/go.mod"

echo "==> 3/3 编译到 $OUT"
mkdir -p "$(dirname "$OUT")"
( cd "$work" && go build -o "$OUT" ./cmd/termshot )
[[ -x "$OUT" ]] || die "编译完成但未找到可执行文件: $OUT"
echo "[✓] $OUT"
