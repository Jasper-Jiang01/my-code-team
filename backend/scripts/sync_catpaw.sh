#!/usr/bin/env bash
# 把 CodePilot 可部署文件同步到 jingwai-agent-main（CatPaw 后端 + Talos 前端）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$(cd "$ROOT/../jingwai-agent-main" && pwd)"
FRONTEND="$(cd "$ROOT/../frontend" && pwd)"

if [[ ! -d "$DEST/.git" ]]; then
  echo "missing jingwai-agent-main git repo at $DEST" >&2
  exit 1
fi

mkdir -p "$DEST/src" "$DEST/checkpoints" "$DEST/.catpaw" "$DEST/frontend"

# CloudNative 构建镜像没有 npm；先在同步机产出 Vite 静态文件，放入 Python 包后随发布仓提交。
npm ci --prefix "$FRONTEND" --no-audit --no-fund
VITE_API_BASE_URL= npm run build --prefix "$FRONTEND"

rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$ROOT/src/" "$DEST/src/"
rsync -a --delete "$FRONTEND/build/" "$DEST/src/codepilot/api/static/"
rsync -a --delete \
  --exclude node_modules \
  --exclude build \
  --exclude dist \
  --exclude .env \
  --exclude .env.local \
  --exclude '*.tsbuildinfo' \
  --exclude .screenshots \
  "$FRONTEND/" "$DEST/frontend/"

cp "$ROOT/server.py" "$DEST/server.py"
cp "$ROOT/.env.example" "$DEST/.env.example"
# 个人测试仓例外：CatPaw CloudNative 无环境变量注入机制（无控制台可配，
# 官方手册未提供 env 字段），模型密钥只能随仓库进入容器。
# app.py 启动时 load_dotenv(仓库根/.env)，pydantic env_file 也指向 ./
# 两套机制都要求 .env 位于仓库根。仅限个人测试仓这样做。
if [[ -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env" "$DEST/.env"
fi
cp "$ROOT/deploy/requirements.txt" "$DEST/requirements.txt"
cp "$ROOT/deploy/catpaw_deploy.yaml" "$DEST/.catpaw/catpaw_deploy.yaml"

# 平台从仓库根 manifest.yaml 读取构建工具链（.catpaw/catpaw_deploy.yaml 的
# build.tools 不随部署工具生成的外部清单传递，2026-09-08 实测仅在其中声明
# gcc-c++ 时容器内仍无 C++ 编译器，contourpy 回退 sdist 编译直接失败）。
# 工具链为兜底：requirements.txt 已把全部 native 依赖钉死为预编译 wheel，
# 正常构建不应触发任何源码编译（CentOS 7 的 gcc 4.8.5 也不支持现代 meson
# 要求的 C++17）。修改工具链时同步更新 deploy/catpaw_deploy.yaml。
cat > "$DEST/manifest.yaml" <<'EOF'
# CloudNative 构建清单 — CodePilot FastAPI（主图 BFF）
type: cloudnative
projectID: jiangwenzhe02-codepilot
python: "3.12"

build:
  tools:
    gcc: "4.8.5"
    gcc-c++: "4.8.5"
    ninja: "1.10.2"
    rust: "1.85.0"
    cargo: "1.85.0"
    libjpeg-turbo-devel: "1.5.3"
  cmd:
    - pip install -i https://pypi.sankuai.com/simple/ --trusted-host pypi.sankuai.com -r requirements.txt

target:
  - ./

runCmd:
  - python server.py

ports:
  - 8000
EOF

# 骨架仓遗留的境外 Demo 与瘦身前目录，避免和 CodePilot 入口混在一起。
# skills 例外：baa-basic 分析 skill（backend/skills/data_analyze）需要随仓发布，
# 供线上 ba_agent_analysis 工具调用其 scripts/call_ba_agent.py。
rm -rf "$DEST/agent" "$DEST/agents" "$DEST/memory"
rm -rf "$DEST/skills"
mkdir -p "$DEST/skills"
if [[ -d "$ROOT/skills" ]]; then
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude 'node_modules' \
    "$ROOT/skills/" "$DEST/skills/"
fi

# Talos 在仓库根执行 npm run build，再收集 build/
cat > "$DEST/package.json" <<'EOF'
{
  "name": "codepilot-web",
  "private": true,
  "scripts": {
    "build": "npm ci --prefix frontend && npm run build --prefix frontend && rm -rf build && cp -R frontend/build build"
  }
}
EOF

cat > "$DEST/.gitignore" <<'EOF'
venv/
.venv/
__pycache__/
*.pyc
.env
artifacts/
# SQLite checkpoint 运行时产物（含 WAL/SHM 伴生文件）
codepilot_checkpoints.db*
*.db-shm
*.db-wal
checkpoints/*
!checkpoints/.gitkeep
memory/vector_store.json
memory/project_memory.json
uploads/
node_modules/
frontend/node_modules/
frontend/build/
frontend/dist/
/build/
EOF

touch "$DEST/checkpoints/.gitkeep"

cat > "$DEST/README.md" <<'EOF'
# CodePilot（内网发布仓）

同一仓库两套发布：

- **CatPaw / Plus**：FastAPI 包装 `main_workflow`，端口 8000。构建工具选 virtualenv。
- **Talos 2.0**：仓库根 `npm run build` 产出 `build/`（实际编译 `frontend/`）。

## 本地后端

```bash
cp .env.example .env
pip install -r requirements.txt
python server.py
```

- `GET /ok` 与 `GET /api/health`
- `POST /api/chat` `{"message":"...","session_id":"..."}` SSE

密钥策略（实测结论）：CatPaw CloudNative 无环境变量注入机制（无控制台、
无 env 字段），因此 .env 随本个人测试仓提交进容器（deploy.sh 会 git add -f）；
同步脚本会把 backend/.env 复制到仓库根。切勿把此做法用于团队仓或线上仓。
单副本默认 SQLite；多副本再配 DATABASE_URL。

开发在 CodePilot `backend/` / `frontend/`，改完后 `make sync-catpaw` 再推本仓 `master`。
EOF

echo "synced -> $DEST"
