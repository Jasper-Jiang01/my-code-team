"""针对高风险决策的人工介入确认节点。"""

import logging

from langgraph.types import interrupt

from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)


def _has_high_risk_issue(state: WorkflowState) -> bool:
    issues = state.get("issues_ledger") or []
    return any(issue.get("risk") == "high" and issue.get("status") != "resolved" for issue in issues)


def human_confirm(state: WorkflowState) -> dict:
    """暂停工作流，等待人工对高风险节点进行确认。

    根据设计文档（第 3.2.6 节）：仅当运行到达此节点时
    ``issues_ledger`` 中存在尚未解决的高风险问题（如生产签字、发布
    门禁等）时，才需要人工确认。如果没有这类问题，运行会自动
    继续而不会被阻塞。

    当触发中断后，调用方必须使用 ``Command(resume=<payload>)``
    恢复运行，其中 ``<payload>`` 是一个字典，如
    ``{"approved": True}`` 或 ``{"approved": False, "comment": "..."}``。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含反映人工决策的 ``human_confirm`` 标志的字典
        （如果没有待处理的高风险问题，则自动为 ``True``）。
    """
    if not _has_high_risk_issue(state):
        return {"human_confirm": True}

    logger.info("human_confirm: high-risk issue(s) detected, interrupting for human review")
    decision = interrupt(
        {
            "reason": "high_risk_issue_pending",
            "issues_ledger": state.get("issues_ledger", []),
            "qa_report": state.get("qa_report"),
            "prompt": "存在未解决的高风险问题，请审核后通过 Command(resume={'approved': bool}) 恢复流程。",
        }
    )

    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else bool(decision)
    return {"human_confirm": approved}
