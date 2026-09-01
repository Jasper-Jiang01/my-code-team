"""评审阶段汇总节点 — 在 ReviewGraph 中汇总三道门禁结果。

在主工作流集成 ReviewGraph 后，此节点的职责已由
``review_steps.finalize_review`` 承担。此模块保留为兼容入口，
内部直接委托给 ``finalize_review``。
"""

from codepilot.nodes.review_steps import finalize_review
from codepilot.states.workflow_state import WorkflowState


def execute_review(state: WorkflowState) -> dict:
    """汇总评审结果，生成 qa_report。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 qa_report 和更新后 issues_ledger 的字典。
    """
    return finalize_review(state)
