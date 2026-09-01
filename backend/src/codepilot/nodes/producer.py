"""方案起草节点 — 为对抗式验证起草一份方案提案。

属于 DecisionGraph 对抗式验证循环（第 4.3 节）的一部分：
``producer -> critic -> judge``。生产者使用独立的 ``producer`` Harness，
不与审核者共用角色。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.context_views import format_state_context
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_PRODUCER_TASK_TEMPLATE = """\
你被授权读取的 State Bus 字段：
{context}

{critique_section}
{variant_section}

请产出（或修订）一份可执行的方案提案（proposal），包含明确的范围与约束。
只输出 JSON，不要输出其他文字，格式为：
{{"goal": "...", "scope": "...", "constraints": ["...", "..."]}}
"""

_CRITIQUE_SECTION_TEMPLATE = """\
上一轮红军组（critic）提出的问题，本轮必须逐条修复：
{critique}
"""


def _parse_proposal(raw_content: str) -> dict | None:
    parsed = extract_json(raw_content)
    if isinstance(parsed, dict) and "goal" in parsed:
        return parsed
    return None


def draft_proposal(state: Mapping[str, Any], variant_hint: str = "") -> dict:
    """根据状态起草一份提案；LLM 失败时回退到 goal/scope/constraints。"""
    goal = str(state.get("goal") or "")
    proposal: dict = {
        "goal": goal,
        "scope": str(state.get("scope") or ""),
        "constraints": list(state.get("constraints") or []),
    }
    critique = state.get("decision_critique")
    critique_section = ""
    if critique:
        critique_section = _CRITIQUE_SECTION_TEMPLATE.format(
            critique=json.dumps(critique, ensure_ascii=False)
        )
    variant_section = f"本候选的生成策略：{variant_hint}" if variant_hint else ""
    try:
        task = _PRODUCER_TASK_TEMPLATE.format(
            context=format_state_context(state, "producer"),
            critique_section=critique_section,
            variant_section=variant_section,
        )
        response = invoke_agent("producer", task)
        parsed = _parse_proposal(safe_content(response))
        if parsed:
            proposal = parsed
            if variant_hint:
                proposal.setdefault("strategy", variant_hint)
    except Exception:  # noqa: BLE001
        logger.exception("draft_proposal: failed via LLM, using deterministic fallback")
        if variant_hint:
            proposal["strategy"] = variant_hint
            extra = list(proposal.get("constraints") or [])
            extra.append(variant_hint)
            proposal["constraints"] = extra
    return proposal


def producer(state: WorkflowState) -> dict:
    """起草（或若已有上轮批评则修订）一份方案提案。"""
    proposal = draft_proposal(state)
    return {
        "decision_proposal": proposal,
        "decision_round": int(state.get("decision_round") or 0) + 1,
    }
