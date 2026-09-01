"""Route the task to the appropriate subgraph based on classification."""

from codepilot.states.workflow_state import WorkflowState


def route_task(state: WorkflowState) -> str:
    """Return the target node name based on the current next_step.

    Args:
        state: The current workflow state.

    Returns:
        The target node name for conditional edge routing.
    """
    return state.get("next_step", "research")
