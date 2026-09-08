.PHONY: install lint typecheck format test run serve sync-catpaw sync-talos deploy

# 统一由 uv 管理 Python 3.12 和锁定依赖，避免调用系统 pip/python。
# install 负责同步环境；其余目标只使用已锁定环境，不在检查时隐式改依赖。
UV_RUN = uv run --frozen --no-sync --extra dev

install:
	uv sync --extra dev

lint:
	$(UV_RUN) ruff check src/ tests/ server.py

typecheck:
	$(UV_RUN) mypy src/

format:
	$(UV_RUN) ruff format src/ tests/ server.py

test:
	$(UV_RUN) pytest tests/ -v

run:
	$(UV_RUN) langgraph dev

# 内网 / CatPaw 同款入口（FastAPI 包主图，默认 :8000）。
# --reload 仅限本地开发，类生产环境请用 python server.py。
serve:
	PYTHONPATH=src $(UV_RUN) uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# 把可部署文件同步到 jingwai-agent-main（独立 Git，推送后走 CatPaw）
sync-catpaw:
	./scripts/sync_catpaw.sh

# 把 frontend 同步到 nibfe/codepilot-web（独立 Git，推 main 后走 Talos）
sync-talos:
	./scripts/sync_talos.sh

# 一键部署 CatPaw CloudNative 测试环境（预检→同步→校验→提交推送→切流验收）
# 详见 deploy/DEPLOY.md。部署触发需配合 CatPaw catpaw_deploy 工具。
deploy:
	bash deploy/deploy.sh
