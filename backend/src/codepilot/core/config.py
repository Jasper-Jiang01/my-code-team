"""应用设置与配置。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """从环境变量中加载运行时设置。"""

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

    # 会话 checkpoint
    database_url: str = ""
    checkpoint_backend: str = "auto"  # auto | memory | sqlite | postgres | platform
    checkpoint_sqlite_path: str = str(_BACKEND_ROOT / "checkpoints" / "main.sqlite")

    # LangSmith（可选）
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "codepilot"


settings = Settings()
