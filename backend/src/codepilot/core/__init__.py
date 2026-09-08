"""核心配置与模型初始化。"""

from codepilot.core.config import Settings
from codepilot.core.llm_utils import extract_json, safe_content

__all__ = ["Settings", "create_chat_model", "extract_json", "safe_content"]


def __getattr__(name: str):
    # 惰性转发：模型工厂位于 codepilot.model，顶层导入会与
    # codepilot.model -> codepilot.core.config 形成循环依赖。
    if name == "create_chat_model":
        from codepilot.model import create_chat_model

        return create_chat_model
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
