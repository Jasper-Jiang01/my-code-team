"""Main workflow graph - the top-level orchestrator."""

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from codepilot.states.workflow_state import WorkflowState
from codepilot.nodes import (
    classify_task,
    route_task,
    execute_research,
    execute_data,
    execute_produce,
    execute_qa,
    human_confirm,
)


def build_main_workflow() -> CompiledStateGraph:
    """Build and return the main workflow StateGraph.

    Note:
        No checkpointer is passed at compile time. When served locally via
        ``langgraph dev`` or deployed to LangGraph Platform, the platform
        injects its own checkpointer/store automatically. Passing one here
        would conflict with that behavior.

    Returns:
        The compiled StateGraph instance.
    """
    builder = StateGraph(WorkflowState)

    # Nodes
    builder.add_node("classify", classify_task)
    builder.add_node("research", execute_research)
    builder.add_node("data", execute_data)
    builder.add_node("produce", execute_produce)
    builder.add_node("qa", execute_qa)
    builder.add_node("human_confirm", human_confirm)

    # Edges
    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify",
        route_task,
        {
            "research": "research",
            "data": "data",
            "produce": "produce",
            "qa": "qa",
        },
    )
    builder.add_edge("research", "data")
    builder.add_edge("data", "produce")
    builder.add_edge("produce", "qa")
    builder.add_edge("qa", END)

    return builder.compile()


# Module-level compiled graph instance, referenced by langgraph.json
# (`./src/codepilot/graphs/main_workflow.py:graph`) so that `langgraph dev` /
# `langgraph up` can discover and serve it without a custom server.
graph = build_main_workflow()
