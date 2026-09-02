"""用于创建对话模型实例的工厂。

按模型名称缓存实例，避免每次调用都新建 ChatOpenAI / ChatAnthropic，
降低 HTTP 客户端与 TLS 建连成本。不同节点可通过 ``max_retries`` 参数
覆盖默认退避策略。
"""

import logging
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from codepilot.core.config import settings

logger = logging.getLogger(__name__)

# 默认超时与重试，防止 LLM 请求永久挂起导致整个图卡死
_DEFAULT_TIMEOUT = 120
_DEFAULT_MAX_RETRIES = 2


class ModelConfigError(RuntimeError):
    """当模型配置缺失或无效时抛出。"""


@lru_cache(maxsize=32)
def _create_chat_model_cached(
    model_name: str,
    timeout: int,
    max_retries: int,
) -> BaseChatModel:
    """按 (model_name, timeout, max_retries) 缓存模型实例。

    LRU 缓存键包含 timeout/max_retries，使不同节点能用不同退避策略
    拿到各自缓存的实例，同时复用底层 HTTP 连接池。
    """
    name_lower = model_name.lower()

    if name_lower.startswith("claude"):
        if not settings.anthropic_api_key:
            raise ModelConfigError(
                "anthropic_api_key is not configured; set it in .env or environment variables"
            )
        return ChatAnthropic(
            model=model_name,
            api_key=settings.anthropic_api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    if name_lower.startswith("longcat"):
        if not settings.longcat_api_key:
            raise ModelConfigError(
                "longcat_api_key is not configured; set it in .env or environment variables"
            )
        # LongCat 兼容 OpenAI API，将 ChatOpenAI 指向其接口地址。
        # 文档：https://longcat.chat/platform/docs/zh/
        return ChatOpenAI(
            model=model_name,
            api_key=settings.longcat_api_key,
            base_url=settings.longcat_base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    # 默认 OpenAI
    if not settings.openai_api_key:
        raise ModelConfigError(
            "openai_api_key is not configured; set it in .env or environment variables"
        )
    return ChatOpenAI(
        model=model_name,
        api_key=settings.openai_api_key,
        timeout=timeout,
        max_retries=max_retries,
    )


def create_chat_model(
    model_name: str | None = None,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> BaseChatModel:
    """根据模型名称创建一个对话模型实例（按配置缓存）。

    Args:
        model_name: 模型标识符。默认为 settings.default_model。
        timeout: 请求超时秒数，防止 LLM 调用永久挂起。
        max_retries: 失败重试次数（含指数退避）。

    Returns:
        一个 BaseChatModel 实例（命中缓存时会复用 HTTP 连接池）。

    Raises:
        ModelConfigError: 当对应的 API Key 未配置时。
    """
    name = model_name or settings.default_model
    return _create_chat_model_cached(name, timeout, max_retries)
