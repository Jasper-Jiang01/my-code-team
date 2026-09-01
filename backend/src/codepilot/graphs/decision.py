"""Decision subgraph - stages 3-4: data测算 and 指标与方案."""

from langgraph.graph import StateGraph

from codepilot.states.workflow_state import WorkflowState


def build_decision_graph() -> StateGraph:
    """Build the decision-making subgraph.

    Returns:
        The compiled StateGraph instance.
    """
    builder = StateGraph(WorkflowState)

    # TODO: Add nodes for metric computation and solution selection
    # TODO: Add adversarial validation (producer vs critic vs judge)
    # TODO: Add loop contract (Spec · Loop Contract)

    return builder.compile()
