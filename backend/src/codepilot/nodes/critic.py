"""评审节点 — 对 producer 提案进行红军挑战。

属于 DecisionGraph 对抗式验证循环（第 4.3 节）的一部分。
critic 使用独立的 ``critic`` Harness，不与 producer 共用角色或 Prompt。
"""

import logging

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.context_views import format_state_context
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 3

_CRITIC_TASK_TEMPLATE = """\
你被授权读取的 State Bus 字段：
{context}

请挑战当前方案提案。只输出 JSON，格式为：
{{"verdict": "pass" | "needs_fix", "issues": ["问题1", "问题2"]}}
"""


def _parse_critique(raw_content: str) -> dict:
    parsed = extract_json(raw_content)
    if isinstance(parsed, dict) and parsed.get("verdict") in ("pass", "needs_fix"):
        return parsed
    return {"verdict": "needs_fix", "issues": ["critic 输出无法解析，默认不放行"]}


def critic(state: WorkflowState) -> dict:
    """挑战当前提案，并给出 pass / needs_fix 的裁决。

    一旦 ``decision_round`` 达到 ``_MAX_ROUNDS`` 就会强制通过，以保证
    producer<->critic 循环一定收敛（循环契约）。
    """
    round_count = state.get("decision_round", 0)

    if round_count >= _MAX_ROUNDS:
        logger.warning("critic: max rounds (%d) reached, force-passing", _MAX_ROUNDS)
        return {
            "decision_critique": {"verdict": "pass", "issues": [], "forced": True},
            "decision_verdict": "pass",
        }

    try:
        response = invoke_agent(
            "critic",
            _CRITIC_TASK_TEMPLATE.format(context=format_state_context(state, "critic")),
        )
        critique = _parse_critique(safe_content(response))
    except Exception:  # noqa: BLE001
        logger.exception("critic: failed to evaluate proposal via LLM")
        critique = {"verdict": "needs_fix", "issues": ["critic 执行失败，默认不放行"]}

    return {
        "decision_critique": critique,
        "decision_verdict": critique.get("verdict", "needs_fix"),
    }
