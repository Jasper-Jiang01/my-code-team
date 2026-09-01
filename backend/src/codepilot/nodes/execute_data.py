"""Data agent node - performs metric calculations and data verification."""

from codepilot.states.workflow_state import WorkflowState


def execute_data(state: WorkflowState) -> dict:
    """Execute the data analysis phase.

    Args:
        state: The current workflow state.

    Returns:
        A dict with updated facts_ledger and evidence.
    """
    # TODO: Implement data agent with SQL query and metric computation
    return {"checkpoints": ["data_done"]}
