"""入口短路：对明确闲聊做零延迟回复，其余放行到 triage。

设计要点（相对旧版）：
1. **模板优先**：问候/感谢/告别等用固定回复，不调 LLM，真正“快”。
2. **``Command`` 路由**：同时写 ``chitchat_reply`` 并 ``goto=END``，或
   ``goto=\"triage\"``；不再占用 ``next_step``（留给 classify）。
3. **严格匹配**：只认白名单正则，去掉「你/我 + ≤10 字」启发式，
   避免「我要做周报」等任务被误判为闲聊。
4. **产品能力说明也走模板**：保证介绍准确、稳定，不依赖模型临场发挥。
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from langgraph.graph import END
from langgraph.types import Command

from codepilot.states.workflow_state import WorkflowState, user_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 意图 → 固定回复（零 LLM）
# ---------------------------------------------------------------------------

_REPLIES: dict[str, str] = {
    "greeting": (
        "你好！我是 CodePilot，多 Agent 编码副驾驶。"
        "可以说目标，我会按「研究 → 决策 → 生产 → 评审」推进。"
    ),
    "thanks": "不客气。有具体任务随时发我。",
    "bye": "再见，下次继续。",
    "ack": "好的，收到。",
    "identity": (
        "我是 CodePilot：帮你澄清问题、锁定规格、产出 Demo、跑质检门禁。"
        "直接描述你想做的事即可（例如「商家周报点击率口径」）。"
    ),
    "empty": "请告诉我你想做什么，例如研究口径、做 Demo 或跑一轮质检。",
    "emoji": "收到 👍",
}

# (意图, 正则) — 按优先级从上到下匹配；全部要求整句贴近闲聊，避免吞掉任务句。
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "greeting",
        re.compile(
            r"^(你好|您好|hi|hello|hey|嗨|哈喽|在吗|在不在)[!！。.~？?\s]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "thanks",
        re.compile(
            r"^(谢谢|感谢|thanks|thx|多谢|辛苦了|谢了)[!！。.~？?\s]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "bye",
        re.compile(
            r"^(再见|拜拜|bye|goodbye|886)[!！。.~？?\s]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "ack",
        re.compile(
            r"^(好的|收到|了解|明白|ok|okay|嗯|ok啦)[!！。.~？?\s]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "identity",
        re.compile(
            r"^(你是谁|你叫什么|你是啥|介绍(一下)?自己|你是什么|"
            r"你能做(什么|啥)?|你能干(什么|啥)?|你会什么|你会做(什么|啥)?|"
            r"你会干(什么|啥)?|你是做(什么|啥)的|"
            r"你有什么(用|功能|能力)?|你都能(做|干)?(什么|啥)?|"
            r"有什么用|你是干嘛的?)[!！。.~？?\s]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "emoji",
        re.compile(r"^(👍|👌|🙌|🎉|😄|哈哈|嘿嘿|呵呵|哦|嗯嗯)\s*$"),
    ),
]


def match_chitchat(goal: str) -> str | None:
    """若为明确闲聊则返回意图键，否则 ``None``（放行工作流）。

    空输入视为引导提示（``empty``），仍短路结束，避免空跑四段流水线。
    """
    text = (goal or "").strip()
    if not text:
        return "empty"
    if len(text) > 40:
        return None
    for intent, pattern in _PATTERNS:
        if pattern.match(text):
            return intent
    return None


def chitchat(
    state: WorkflowState,
) -> Command[Literal["triage", "__end__"]]:
    """入口闸门：闲聊模板回复并结束；否则放行到 ``triage``。"""
    text = user_text(state)
    intent = match_chitchat(text)
    hydrated = {"userMessage": text, "goal": text}

    if intent is None:
        logger.debug("chitchat: pass-through to triage, goal=%r", text[:80])
        return Command(update=hydrated, goto="triage")

    reply = _REPLIES[intent]
    logger.info("chitchat: short-circuit intent=%s goal=%r", intent, text[:40])
    return Command(
        update={
            **hydrated,
            "chitchat_reply": reply,
            "checkpoints": [f"chitchat:{intent}"],
        },
        goto=END,
    )
