"""Human-in-the-loop confirmation node for high-risk decisions."""

from codepilot.states.workflow_state import WorkflowState


def human_confirm(state: WorkflowState) -> dict:
    """Pause the workflow and wait for human confirmation.

    Args:
        state: The current workflow state.

    Returns:
        A dict with the human_confirm flag.
    """
    # TODO: Implement interrupt/resume mechanism via LangGraph interrupt
    return {"human_confirm": True}
