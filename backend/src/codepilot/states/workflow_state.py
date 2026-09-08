"""最小 DeepAgent 主图的输入与状态。"""

from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field


class WorkflowInput(BaseModel):
    """聊天 API 和 LangGraph 的唯一入口。"""

    model_config = ConfigDict(extra="ignore")

    userMessage: str = Field(min_length=1, description="用户输入，纯文本即可")
    # 显式指定意图（如前端快捷入口）时跳过意图分类，直接进入对应子图；
    # 留空则由 intent_route 节点自动判定。仅接受已知标签，非法值忽略。
    intent: str = Field(default="", description="可选：显式意图（data_analysis / general）")


def user_text(state: Mapping[str, Any]) -> str:
    """读取并规范化用户输入。"""
    return str(state.get("userMessage") or "").strip()


class WorkflowState(TypedDict):
    """``START → prelude → intent_route → {deep_agent | data_analysis} → epilogue → END`` 的状态。

    ``messages`` 是与各执行器子图共享的通道：prelude 写入用户消息，
    子图的模型输出与工具消息经 ``add_messages`` 累积，epilogue 从中
    提取最终答复。子图内 HumanInTheLoop 的 interrupt 也经由该节点
    冒泡到主图。

    ``intent`` 由 intent_route 节点写入，记录意图分类结果
    （``general`` / ``data_analysis``），epilogue 据此标记实际执行器。
    """

    userMessage: str
    chitchat_reply: str
    checkpoints: list[str]
    intent: str
    messages: Annotated[list[AnyMessage], add_messages]
