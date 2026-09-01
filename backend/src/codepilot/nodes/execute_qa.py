"""QA agent node - runs the three quality gates (function, visual, rehearsal)."""

from codepilot.states.workflow_state import WorkflowState


def execute_qa(state: WorkflowState) -> dict:
    """Execute the QA phase with three quality gates.

    Args:
        state: The current workflow state.

    Returns:
        A dict with updated qa_report and issues_ledger.
    """
    # TODO: Implement QA agent with screenshot diff and functional tests
    return {"checkpoints": ["qa_done"]}
