"""Application settings and configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    longcat_api_key: str = ""
    longcat_base_url: str = "https://api.longcat.chat/openai"
    default_model: str = "gpt-4o"

    # Database
    database_url: str = "postgresql://user:password@localhost:5432/codepilot"

    # Vector Store
    vector_store_url: str = "http://localhost:6333"

    # LangSmith
    langsmith_api_key: str = ""

    # Internal Tools
    km_search_endpoint: str = ""
    sql_query_endpoint: str = ""


settings = Settings()
