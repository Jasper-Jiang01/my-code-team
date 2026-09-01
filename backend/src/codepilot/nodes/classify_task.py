"""Classify the incoming task and determine the next workflow step."""

from codepilot.states.workflow_state import WorkflowState


def classify_task(state: WorkflowState) -> dict:
    """Classify the task goal and set the next step.

    Args:
        state: The current workflow state.

    Returns:
        A dict with the updated next_step.
    """
    # TODO: Implement LLM-based task classification
    return {"next_step": "research"}
