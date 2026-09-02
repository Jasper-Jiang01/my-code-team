"""简单对话短路节点 — 对闲聊/问候类输入直接回复，跳过完整流水线。

当用户输入属于简单对话（如"你好""谢谢""你是谁"等）时，本节点直接
用 LLM 生成一条简短回复，将回复文本写入 ``chitchat_reply`` 字段，
同时设置 ``next_step = "end"`` 使主工作流立即终止，不进入
research → data → produce → qa 的完整闭环。

前端通过 ``updates`` 流捕获 chitchat 节点的返回值，其中
``chitchat_reply`` 字段会被前端 ``mapUpdates`` 映射为 token 事件
展示给用户。

对于非简单对话，本节点放行到 ``classify`` 节点走正常分类流程。
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from codepilot.core.create_model import create_chat_model
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 简单对话模式匹配
# ---------------------------------------------------------------------------

_SIMPLE_PATTERNS: list[str] = [
    # 问候
    r"^(你好|您好|hi|hello|hey|嗨|哈喽|在吗|在不在)\s*[!！。.~]?$",
    # 感谢
    r"^(谢谢|感谢|thanks|thx|多谢|辛苦了|谢了)\s*[!！。.~]?$",
    # 身份询问 / 能力询问（覆盖 "你会什么""你能做什么""你有什么用" 等）
    r"^(你是谁|你叫什么|你是啥|介绍.*自己|你是什么|你能做|你能干|你会什么|你会干|你有什么|你都能|有什么用|你是干嘛)",
    # 告别
    r"^(再见|拜拜|bye|goodbye|886)\s*[!！。.~]?$",
    # 确认/收到
    r"^(好的|收到|了解|明白|ok|okay|嗯|ok啦)\s*[!！。.~]?$",
    # 单纯表情 / 语气词
    r"^(👍|👌|🙌|🎉|😄|哈哈|嘿嘿|呵呵|哦|嗯嗯)\s*$",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _SIMPLE_PATTERNS]

_CHITCHAT_SYSTEM_PROMPT = """\
你是 CodePilot，一个多 Agent 协作的 AI 编码副驾驶。用户刚才发了一条简短的\
闲聊消息，请友好、简短地回复（1-3 句话即可），并自然地引导用户提出技术问题\
或任务需求。不要输出 JSON，不要调用工具，直接用自然语言回复。"""


def _is_chitchat(goal: str) -> bool:
    """判断输入是否属于简单对话。

    采用两层检测：
    1. 正则模式匹配（精确覆盖已知问候/感谢/身份询问等）
    2. 启发式兜底：极短输入（≤10 字）且含有自称/人称代词时，视为闲聊

    Args:
        goal: 用户输入的目标文本。

    Returns:
        如果匹配任何简单对话模式则为 True。
    """
    text = goal.strip()
    if not text or len(text) > 50:
        return False
    if any(pattern.match(text) for pattern in _COMPILED):
        return True
    # 启发式兜底：极短（≤10 字）且以人称代词开头 → 闲聊
    # 只匹配 "你..." "我..." 开头的输入（如 "你会什么""你是谁"），
    # 避免 "帮我查数据" 等含代词的任务请求被误判。
    if len(text) <= 10 and re.match(r"^[你我他她它]", text):
        return True
    return False


def chitchat(state: WorkflowState) -> dict:
    """对简单对话直接用 LLM 生成回复，短路完整流水线。

    如果输入不是简单对话，返回空 next_step 表示放行到 classify。
    如果输入是简单对话，用 LLM 生成回复并将文本写入
    ``chitchat_reply``，前端通过 ``updates`` 流读取该字段。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 ``next_step`` 的字典：
        - ``"end"`` 表示短路结束（chitchat 已回复）；
        - ``""`` 表示放行到正常分类流程。
    """
    goal = state.get("goal", "")

    if not _is_chitchat(goal):
        # 非简单对话，放行到 classify
        return {"next_step": ""}

    logger.info("chitchat: short-circuiting simple input: %r", goal)

    # 用 LLM 生成自然回复（闲聊是轻量调用，缩短超时与重试）
    reply_text = ""
    try:
        model = create_chat_model(timeout=30, max_retries=1)
        messages = [
            SystemMessage(content=_CHITCHAT_SYSTEM_PROMPT),
            HumanMessage(content=goal),
        ]
        response = model.invoke(messages)
        # 从 response 中提取文本内容
        reply_text = getattr(response, "content", "") or str(response)
    except Exception:  # noqa: BLE001 - chitchat 失败不应阻塞图
        logger.exception("chitchat: LLM reply failed, will fall through to classify")
        # LLM 失败时放行到正常流程，至少不会卡住
        return {"next_step": ""}

    # 标记短路结束，主工作流的条件边会路由到 END。
    # chitchat_reply 会通过 updates 流推给前端，前端 mapUpdates 会识别它。
    return {
        "next_step": "end",
        "chitchat_reply": reply_text,
        "checkpoints": ["chitchat_short_circuit"],
    }
