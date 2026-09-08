"""兼容转发：模型初始化逻辑已迁移至 ``codepilot.model.create_model``。"""

from codepilot.model.create_model import (
    ModelConfigError,
    _create_chat_model_cached,
    create_chat_model,
    init_chat_model,
)

__all__ = [
    "ModelConfigError",
    "_create_chat_model_cached",
    "create_chat_model",
    "init_chat_model",
]
