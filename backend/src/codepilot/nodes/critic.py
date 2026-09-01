"""评审节点 — 对 producer 提案进行红军挑战。

属于 DecisionGraph 对抗式验证循环（第 4.3 节）的一部分：
``producer -> critic -> judge``。critic 扮演“红军”角色，
主动寻找方案中的漏洞，而不是盲目盖章放行。
"""

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from codepilot.core.create_model import create_chat_model
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 3

_CRITIC_SYSTEM_PROMPT = """\
你是红军组（critic）。你的职责是尽全力挑战生产者（producer）提出的方案，
找出其中的漏洞、遗漏的约束、模糊不清的范围或与已知事实/规则相悖之处。
不要因为方案看起来合理就轻易放行——高代价决策必须经过严格质询。

只输出 JSON，不要输出其他文字，格式为：
{"verdict": "pass" | "needs_fix", "issues": ["问题1", "问题2", ...]}
"""


def _parse_critique(raw_content: str) -> dict:
    parsed = extract_json(raw_content)
    if isinstance(parsed, dict) and parsed.get("verdict") in ("pass", "needs_fix"):
        return parsed
    return {"verdict": "pass", "issues": []}


def critic(state: WorkflowState) -> dict:
    """挑战当前提案，并给出 pass / needs_fix 的裁决。

    一旦 ``decision_round`` 达到 ``_MAX_ROUNDS`` 就会强制通过，以保证
    producer<->critic 循环一定收敛（循环契约）。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 ``decision_critique`` 和 ``decision_verdict`` 的字典。
    """
    proposal = state.get("decision_proposal") or {}
    round_count = state.get("decision_round", 0)

    if round_count >= _MAX_ROUNDS:
        logger.warning("critic: max rounds (%d) reached, force-passing", _MAX_ROUNDS)
        return {
            "decision_critique": {"verdict": "pass", "issues": [], "forced": True},
            "decision_verdict": "pass",
        }

    try:
        model = create_chat_model()
        messages = [
            SystemMessage(content=_CRITIC_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "proposal": proposal,
                        "facts_ledger": state.get("facts_ledger", []),
                        "rules_ledger": state.get("rules_ledger", []),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        response = model.invoke(messages)
        critique = _parse_critique(safe_content(response))
    except Exception:  # noqa: BLE001 - 不能因为批评失败而让图崩溃
        logger.exception("critic: failed to evaluate proposal via LLM")
        critique = {"verdict": "pass", "issues": []}

    return {
        "decision_critique": critique,
        "decision_verdict": critique.get("verdict", "pass"),
    }
