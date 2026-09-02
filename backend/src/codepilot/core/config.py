"""应用设置与配置。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """从环境变量中加载的应用设置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 大语言模型
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    longcat_api_key: str = ""
    longcat_base_url: str = "https://api.longcat.chat/openai"
    default_model: str = "LongCat-2.0"

    # 数据库 / Checkpoint
    database_url: str = ""
    checkpoint_backend: str = "auto"  # auto | memory | sqlite | postgres | platform
    checkpoint_sqlite_path: str = "codepilot_checkpoints.db"

    # 向量存储
    vector_store_url: str = "http://localhost:6333"

    # LangSmith
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "codepilot"

    # 内部工具
    km_search_endpoint: str = ""
    km_mis: str = ""
    km_fetch_body_top_k: int = 2
    km_snippet_max_chars: int = 1500
    km_citadel_timeout: float = 60.0
    sql_query_endpoint: str = ""
    mcp_endpoint: str = ""
    # 点评 PDE Agent（页面原型 / 设计稿）
    # 操作指南：https://km.sankuai.com/collabpage/2776444575
    pde_endpoint: str = ""
    pde_timeout: float = 60.0

    # 生产阶段产物落盘目录。默认写 backend/artifacts，避免 macOS 对桌面目录无写权限。
    artifacts_dir: str = str(_BACKEND_ROOT / "artifacts")


settings = Settings()


def writable_artifacts_dir() -> Path:
    """返回一个确实可写的产物目录。桌面被沙箱拦住时回退到仓库 ``artifacts/``。"""
    candidates = []
    configured = (settings.artifacts_dir or "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.extend((_BACKEND_ROOT / "artifacts", Path.cwd() / "artifacts"))
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            resolved.mkdir(parents=True, exist_ok=True)
            probe = resolved / ".codepilot_write"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return resolved
        except OSError:
            continue
    return Path.cwd()
