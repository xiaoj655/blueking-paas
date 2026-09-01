#!/usr/bin/env bash
# 安装 bk-cli（TE 内部环境需注入 BK_TE_DOMAIN，否则 bkpaas3 网关 403）
set -euo pipefail

REPO="${REPO:-git@github.com:TencentBlueKing/bk-cli.git}"
DIR="${DIR:-$HOME/repo/bk-cli}"
PREFIX="${PREFIX:-$HOME/.local}"
BK_TE_DOMAIN="${BK_TE_DOMAIN:-o.woa.com}"

if [ ! -d "$DIR/.git" ]; then
  mkdir -p "$(dirname "$DIR")"
  git clone "$REPO" "$DIR"
else
  git -C "$DIR" pull --ff-only
fi

cd "$DIR"
make build VERSION="$(git describe --tags --always 2>/dev/null || echo dev)" BK_TE_DOMAIN="$BK_TE_DOMAIN"
install -d "$PREFIX/bin"
install -m755 bk-cli "$PREFIX/bin/bk-cli"
echo "OK: $PREFIX/bin/bk-cli -> $(git -C "$DIR" rev-parse --short HEAD)"
"$PREFIX/bin/bk-cli" paas get_minimal_app_list >/dev/null 2>&1 \
  && echo "smoke: paas gateway OK" \
  || echo "smoke: paas 请求失败，检查 bk-cli auth login / 网络后重试"
