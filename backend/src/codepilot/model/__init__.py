"""模型初始化包：统一入口为 ``codepilot.model.init_chat_model``。"""

from codepilot.model.create_model import (
    ModelConfigError,
    create_chat_model,
    init_chat_model,
)

__all__ = ["ModelConfigError", "create_chat_model", "init_chat_model"]
