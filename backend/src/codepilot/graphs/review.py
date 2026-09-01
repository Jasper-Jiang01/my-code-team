"""Review subgraph - stages 7-9: red/blue team, QA, and defense."""

from langgraph.graph import StateGraph

from codepilot.states.workflow_state import WorkflowState


def build_review_graph() -> StateGraph:
    """Build the review subgraph with adversarial validation.

    Returns:
        The compiled StateGraph instance.
    """
    builder = StateGraph(WorkflowState)

    # TODO: Add five-panel review perspectives
    # TODO: Add six-round challenge loop (attack -> respond -> judge -> fix -> evidence close)
    # TODO: Add loop-until-done for high-risk issues

    return builder.compile()
