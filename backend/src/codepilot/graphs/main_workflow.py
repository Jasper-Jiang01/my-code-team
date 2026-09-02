"""主工作流图 — 顶层编排器。"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.graphs.decision import build_decision_graph
from codepilot.graphs.problem_discovery import build_problem_discovery_graph
from codepilot.graphs.production import build_production_graph
from codepilot.graphs.review import build_review_graph
from codepilot.nodes import (
    after_qa,
    chitchat,
    classify_task,
    fast_qa,
    human_confirm,
    mark_decision_snapshot,
    mark_production_snapshot,
    route_task,
    route_after_decision,
    route_after_production,
    route_after_research,
    triage,
)
from codepilot.states.workflow_state import WorkflowInput, WorkflowState

# 主图含 QA↔data/produce 闭环与子图内部循环，给足步数并硬封顶
_DEFAULT_RECURSION_LIMIT = 80


def build_main_workflow(checkpointer: object | None = None) -> CompiledStateGraph:
    """构建并返回主工作流 StateGraph。

    四个闭环按 ``research -> data -> produce -> qa`` 推进；入口先经
    ``chitchat`` / ``triage``：闲聊与简单问答短路结束。明确的原型 / 需求 /
    代码 / 质检意图由 triage 直达对应子图并按工具白名单调用；只有完整 Demo
    才走四段链。QA 结束后由 ``after_qa``（Command）消费 ``qa_reopen_target``
    / 指纹兜底，决定重跑 Decision / Production 或进入人工确认。最多额外
    rerun 2 次。
    """
    builder = StateGraph(WorkflowState, input_schema=WorkflowInput)

    builder.add_node("chitchat", chitchat)
    builder.add_node("triage", triage)
    builder.add_node("fast_qa", fast_qa)
    builder.add_node("classify", classify_task)
    builder.add_node("research", build_problem_discovery_graph())
    builder.add_node("data", build_decision_graph())
    builder.add_node("mark_decision", mark_decision_snapshot)
    builder.add_node("produce", build_production_graph())
    builder.add_node("mark_production", mark_production_snapshot)
    builder.add_node("qa", build_review_graph())
    builder.add_node("after_qa", after_qa)
    builder.add_node("human_confirm", human_confirm)

    # 入口：chitchat / triage / fast_qa 均用 Command 跳转，不挂静态条件边。
    builder.set_entry_point("chitchat")
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
    builder.add_conditional_edges(
        "research",
        route_after_research,
        {"data": "data", "end": END},
    )
    builder.add_edge("data", "mark_decision")
    builder.add_conditional_edges(
        "mark_decision",
        route_after_decision,
        {"produce": "produce", "end": END},
    )
    builder.add_edge("produce", "mark_production")
    builder.add_conditional_edges(
        "mark_production",
        route_after_production,
        {"qa": "qa", "end": END},
    )
    builder.add_edge("qa", "after_qa")
    # after_qa 通过 Command(goto=...) 跳转 data / produce / human_confirm，
    # 不再挂静态条件边，避免与动态边双执行。
    builder.add_edge("human_confirm", END)

    return builder.compile(checkpointer=checkpointer).with_config(
        {"recursion_limit": _DEFAULT_RECURSION_LIMIT}
    )


# LangGraph Platform 通过 langgraph.json 加载本对象；不在进程内挂 checkpointer，
# 由平台注入。本地脚本请 ``build_main_workflow(checkpointer=create_checkpointer())``。
graph = build_main_workflow()
