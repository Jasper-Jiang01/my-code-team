"""应用设置与配置。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    default_model: str = "gpt-4o"

    # 数据库
    database_url: str = "postgresql://user:password@localhost:5432/codepilot"

    # 向量存储
    vector_store_url: str = "http://localhost:6333"

    # LangSmith
    langsmith_api_key: str = ""

    # 内部工具
    km_search_endpoint: str = ""
    sql_query_endpoint: str = ""

    # 生产阶段产物落盘目录（用于本地测试查看 Demo 产物/截图等）
    artifacts_dir: str = str(Path.home() / "Desktop" / "CodePilot_artifacts")


settings = Settings()
