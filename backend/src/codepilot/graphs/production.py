"""Production subgraph - stages 5-6: spec design and high-fidelity demo."""

from langgraph.graph import StateGraph

from codepilot.states.workflow_state import WorkflowState


def build_production_graph() -> StateGraph:
    """Build the production subgraph (static 6-step sub-flow).

    Returns:
        The compiled StateGraph instance.
    """
    builder = StateGraph(WorkflowState)

    # TODO: Add 6-step static sub-flow nodes:
    # 01 需求增量, 02 设计草稿, 03 DP 审核,
    # 04 开发实现, 05 视觉还原, 06 QA 验收
    # TODO: Add model role nodes: EXPLORE / GENERATE / BUILD / COMPARE / GUARD / VERIFY

    return builder.compile()
