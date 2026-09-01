"""裁决节点 — 将经 critic 批准的提案裁定为锁定的 Spec。

属于 DecisionGraph 对抗式验证循环（第 4.3 节）的一部分：
``producer -> critic -> judge``。仅在 critic 的裁决为 ``pass`` 时才会
到达此处。锁定最终的 ``Spec`` 和 ``Evidence``，供下游
``ProductionGraph`` 使用。
"""

import logging

from codepilot.states.workflow_state import Evidence, Spec, WorkflowState

logger = logging.getLogger(__name__)


def judge(state: WorkflowState) -> dict:
    """将已批准的提案锁定为 ``spec`` 和 ``evidence``。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含锁定后的 ``spec``、``evidence``，以及完成 checkpoint
        （按照设计文档的 checkpoint 命名规范为 ``SPEC_LOCKED`` /
        ``EVIDENCE_READY``）的字典。
    """
    proposal = state.get("decision_proposal") or {}

    spec: Spec = {
        "goal": proposal.get("goal", state.get("goal", "")),
        "scope": proposal.get("scope", state.get("scope", "")),
        "constraints": list(proposal.get("constraints") or state.get("constraints") or []),
    }
    evidence: Evidence = {
        "facts": list(state.get("facts_ledger", [])),
        "rules": list(state.get("rules_ledger", [])),
    }

    return {
        "spec": spec,
        "evidence": evidence,
        "checkpoints": ["SPEC_LOCKED", "EVIDENCE_READY"],
    }
