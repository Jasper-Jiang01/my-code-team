"""Problem Discovery subgraph - stages 1-2: clarify the problem."""

from langgraph.graph import StateGraph, END

from codepilot.states.workflow_state import WorkflowState


def build_problem_discovery_graph() -> StateGraph:
    """Build the problem discovery subgraph.

    Returns:
        The compiled StateGraph instance.
    """
    builder = StateGraph(WorkflowState)

    # TODO: Add nodes for proposition interpretation and internal/external research
    # TODO: Add fan-out for seed query divergence and keyword network building

    return builder.compile()
