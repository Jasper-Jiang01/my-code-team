"""用于创建对话模型实例的工厂。"""

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from codepilot.core.config import settings


def create_chat_model(model_name: str | None = None) -> BaseChatModel:
    """根据模型名称创建一个对话模型实例。

    Args:
        model_name: 模型标识符。默认为 settings.default_model。

    Returns:
        一个 BaseChatModel 实例。
    """
    name = model_name or settings.default_model

    if name.startswith("claude"):
        return ChatAnthropic(
            model=name,
            api_key=settings.anthropic_api_key,
        )

    if name.lower().startswith("longcat"):
        # LongCat 兼容 OpenAI API，将 ChatOpenAI 指向其接口地址。
        # 文档：https://longcat.chat/platform/docs/zh/
        return ChatOpenAI(
            model=name,
            api_key=settings.longcat_api_key,
            base_url=settings.longcat_base_url,
        )

    return ChatOpenAI(
        model=name,
        api_key=settings.openai_api_key,
    )
