"""主工作流图 — 顶层编排器。"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.graphs.decision import build_decision_graph
from codepilot.graphs.problem_discovery import build_problem_discovery_graph
from codepilot.graphs.production import build_production_graph
from codepilot.graphs.review import build_review_graph
from codepilot.nodes import (
    chitchat,
    classify_task,
    human_confirm,
    mark_decision_snapshot,
    mark_production_snapshot,
    route_after_qa,
    route_task,
)
from codepilot.states.workflow_state import WorkflowState


def build_main_workflow(checkpointer: object | None = None) -> CompiledStateGraph:
    """构建并返回主工作流 StateGraph。

    四个闭环按 ``research -> data -> produce -> qa`` 推进；QA 结束后若
    ``facts_ledger`` 相对决策快照有更新则重跑 DecisionGraph，若 ``spec``
    相对生产快照有更新则回归 ProductionGraph。最多额外 rerun 2 次。
    """
    builder = StateGraph(WorkflowState)

    # ★ chitchat：简单对话短路节点，放在 classify 之前
    builder.add_node("chitchat", chitchat)
    builder.add_node("classify", classify_task)
    builder.add_node("research", build_problem_discovery_graph())
    builder.add_node("data", build_decision_graph())
    builder.add_node("mark_decision", mark_decision_snapshot)
    builder.add_node("produce", build_production_graph())
    builder.add_node("mark_production", mark_production_snapshot)
    builder.add_node("qa", build_review_graph())
    builder.add_node("human_confirm", human_confirm)

    # 入口改为 chitchat：简单对话直接短路到 END，复杂问题放行到 classify
    builder.set_entry_point("chitchat")
    builder.add_conditional_edges(
        "chitchat",
        lambda state: "__end__" if state.get("next_step") == "end" else "classify",
        {
            "__end__": END,
            "classify": "classify",
        },
    )
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
    builder.add_edge("data", "mark_decision")
    builder.add_edge("mark_decision", "produce")
    builder.add_edge("produce", "mark_production")
    builder.add_edge("mark_production", "qa")
    builder.add_conditional_edges(
        "qa",
        route_after_qa,
        {
            "data": "data",
            "produce": "produce",
            "human_confirm": "human_confirm",
        },
    )
    builder.add_edge("human_confirm", END)

    return builder.compile(checkpointer=checkpointer)


# LangGraph Platform 通过 langgraph.json 加载本对象；不在进程内挂 checkpointer，
# 由平台注入。本地脚本请 ``build_main_workflow(checkpointer=create_checkpointer())``。
graph = build_main_workflow()
