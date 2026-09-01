"""方案起草节点 — 为对抗式验证起草一份方案提案。

属于 DecisionGraph 对抗式验证循环（第 4.3 节）的一部分：
``producer -> critic -> judge``，当 ``critic`` 发现尚未解决的问题时
（``needs_fix``），可以将流程送回 ``producer``。
"""

import json
import logging

from codepilot.core.agent_loader import invoke_agent
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_PRODUCER_TASK_TEMPLATE = """\
目标（goal）：{goal}
范围（scope）：{scope}

已知事实（facts_ledger）：
{facts}

已知规则（rules_ledger）：
{rules}

{critique_section}

请产出（或修订）一份可执行的方案提案（proposal），包含明确的范围与约束。
只输出 JSON，不要输出其他文字，格式为：
{{"goal": "...", "scope": "...", "constraints": ["...", "..."]}}
"""

_CRITIQUE_SECTION_TEMPLATE = """\
上一轮红军组（critic）提出的问题，本轮必须逐条修复：
{critique}
"""


def _parse_proposal(raw_content: str) -> dict | None:
    try:
        parsed = json.loads(raw_content)
        if isinstance(parsed, dict) and "goal" in parsed:
            return parsed
    except json.JSONDecodeError:
        logger.warning("producer: failed to parse proposal JSON from LLM output")
    return None


def producer(state: WorkflowState) -> dict:
    """起草（或若已有上轮批评则修订）一份方案提案。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含更新后 ``decision_proposal`` 和自增后
        ``decision_round`` 计数器的字典。
    """
    goal = state.get("goal", "")
    critique = state.get("decision_critique")

    critique_section = ""
    if critique:
        critique_section = _CRITIQUE_SECTION_TEMPLATE.format(
            critique=json.dumps(critique, ensure_ascii=False)
        )

    proposal: dict = {
        "goal": goal,
        "scope": state.get("scope", ""),
        "constraints": list(state.get("constraints") or []),
    }

    try:
        task = _PRODUCER_TASK_TEMPLATE.format(
            goal=goal,
            scope=state.get("scope", "未指定"),
            facts=json.dumps(state.get("facts_ledger", []), ensure_ascii=False),
            rules=json.dumps(state.get("rules_ledger", []), ensure_ascii=False),
            critique_section=critique_section,
        )
        response = invoke_agent("data", task)
        content = response.content if isinstance(response.content, str) else str(response.content)
        parsed = _parse_proposal(content)
        if parsed:
            proposal = parsed
    except Exception:  # noqa: BLE001 - 回退到上面的确定性提案
        logger.exception("producer: failed to draft proposal via LLM")

    return {
        "decision_proposal": proposal,
        "decision_round": state.get("decision_round", 0) + 1,
    }
