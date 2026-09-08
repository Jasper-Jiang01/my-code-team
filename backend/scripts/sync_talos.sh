#!/usr/bin/env bash
# 把 CodePilot frontend 同步到 nibfe/codepilot-web（Talos 发布仓）。
# 推送目标分支是 main（nibfe 的 master 受保护）。改完后自行 git push。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND="$(cd "$ROOT/../frontend" && pwd)"
DEST="$(cd "$ROOT/../codepilot-web" && pwd)"

if [[ ! -d "$DEST/.git" ]]; then
  echo "missing codepilot-web git repo at $DEST" >&2
  echo "clone: git clone -b main ssh://git@git.sankuai.com/nibfe/codepilot-web.git $DEST" >&2
  exit 1
fi

rsync -a --delete \
  --exclude .git \
  --exclude node_modules \
  --exclude build \
  --exclude dist \
  --exclude .env \
  --exclude .env.local \
  --exclude '*.tsbuildinfo' \
  "$FRONTEND/" "$DEST/"

echo "synced -> $DEST"
echo "next: cd $DEST && git add -A && git commit && git push origin HEAD:main"
