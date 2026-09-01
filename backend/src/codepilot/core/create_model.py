"""Factory for creating chat model instances."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

from codepilot.core.config import settings


def create_chat_model(model_name: str | None = None) -> BaseChatModel:
    """Create a chat model instance based on the model name.

    Args:
        model_name: The model identifier. Defaults to settings.default_model.

    Returns:
        A BaseChatModel instance.
    """
    name = model_name or settings.default_model

    if name.startswith("claude"):
        return ChatAnthropic(
            model=name,
            api_key=settings.anthropic_api_key,
        )

    if name.lower().startswith("longcat"):
        # LongCat is OpenAI API-compatible; point ChatOpenAI at its endpoint.
        # Docs: https://longcat.chat/platform/docs/zh/
        return ChatOpenAI(
            model=name,
            api_key=settings.longcat_api_key,
            base_url=settings.longcat_base_url,
        )

    return ChatOpenAI(
        model=name,
        api_key=settings.openai_api_key,
    )
