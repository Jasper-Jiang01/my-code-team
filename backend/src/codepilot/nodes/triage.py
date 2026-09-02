"""入口分流：按需求意图选工具与入口，而不是一律进四段思考链。"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.types import Command

from codepilot.core.intent_router import match_intent
from codepilot.states.workflow_state import WorkflowState, user_text

logger = logging.getLogger(__name__)

_COMPLEX_ENTRIES = {"research", "data", "produce", "qa"}


def match_complexity(goal: str) -> Literal["fast_qa", "complex"]:
    """兼容旧测试：问答走快路径，其余视为复杂任务。"""
    return "fast_qa" if match_intent(goal).entry == "fast_qa" else "complex"


def triage(
    state: WorkflowState,
) -> Command[Literal["fast_qa", "classify", "research", "data", "produce", "qa"]]:
    """按意图写入工具白名单，并跳到对应入口。

    完整 Demo 仍交给 classify（台账续跑）；出原型 / 写需求 / 实现代码
    等明确意图则直达对应子图，避免先跑 search_km / query_sql。
    """
    goal = user_text(state)
    intent = match_intent(goal)
    update = {
        "task_intent": intent.kind,
        "needed_tools": list(intent.tools),
        "pde_stage": intent.pde_stage,
    }
    logger.info(
        "triage: kind=%s entry=%s tools=%s goal=%r",
        intent.kind,
        intent.entry,
        intent.tools,
        (goal or "")[:80],
    )
    if intent.entry == "fast_qa":
        return Command(update=update, goto="fast_qa")
    if intent.kind != "full" and intent.entry in _COMPLEX_ENTRIES:
        return Command(update={**update, "next_step": intent.entry}, goto=intent.entry)
    return Command(update=update, goto="classify")
