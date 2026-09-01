"""Research agent node - gathers evidence and builds the facts ledger."""

from codepilot.states.workflow_state import WorkflowState


def execute_research(state: WorkflowState) -> dict:
    """Execute the research phase.

    Args:
        state: The current workflow state.

    Returns:
        A dict with updated evidence and facts_ledger.
    """
    # TODO: Implement research agent with vector search and KM retrieval
    return {"checkpoints": ["research_done"]}
