"""主工作流图 — 顶层编排器。"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.graphs.decision import build_decision_graph
from codepilot.graphs.problem_discovery import build_problem_discovery_graph
from codepilot.graphs.production import build_production_graph
from codepilot.graphs.review import build_review_graph
from codepilot.nodes import (
    classify_task,
    human_confirm,
    route_task,
)
from codepilot.states.workflow_state import WorkflowState

# 带有 `operator.add`（列表拼接）reducer 的字段。当两个共享父图
# WorkflowState schema 的已编译子图作为兄弟节点串联时（例如
# `research` -> `data`），LangGraph 会在下一个兄弟子图运行时重放
# 第一个子图的内部通道更新，这会导致这些 reducer 在父图层级被
# 重复应用。通过包装每次子图调用并仅重新发出*增量*部分（见
# `_invoke_subgraph_as_node`），可以在不放弃共享状态子图的情况下
# 避免这个问题。
_LIST_REDUCER_FIELDS = (
    "facts_ledger",
    "rules_ledger",
    "issues_ledger",
    "checkpoints",
    "review_panel_results",
    "review_issues",
)


def _invoke_subgraph_as_node(subgraph: CompiledStateGraph):
    """包装一个已编译的子图，使其可以安全地用作父图节点。

    Args:
        subgraph: 已编译的子图（必须与父图共享 WorkflowState schema）。

    Returns:
        一个适用于 ``builder.add_node`` 的普通节点函数。
    """

    def _node(state: WorkflowState) -> dict:
        result = subgraph.invoke(state)
        delta: dict = {}
        for key, value in result.items():
            if key in _LIST_REDUCER_FIELDS and isinstance(value, list):
                before = state.get(key) or []
                delta[key] = value[len(before):]
            elif key not in state or state.get(key) != value:
                delta[key] = value
        return delta

    return _node


def build_main_workflow(checkpointer: object | None = None) -> CompiledStateGraph:
    """构建并返回主工作流 StateGraph。

    设计文档中描述的四个闭环（研究组/数据组+红军组/生产组/质检组+评委）
    严格按顺序执行（``research -> data -> produce -> qa``）；
    ``classify_task`` + ``route_task`` 只决定本次运行应从哪个阶段*进入*
    （例如如果已有研究事实则直接跳转到 ``data``），而不决定哪些阶段会执行。

    四个阶段分别接入已编译的 ``ProblemDiscoveryGraph``、
    ``DecisionGraph``、``ProductionGraph`` 和 ``ReviewGraph`` 子图，
    各自通过 ``_invoke_subgraph_as_node`` 包装，以避免在串联子图节点时
    重复计入列表 reducer 字段（详见上方模块级文档字符串）。

    Args:
        checkpointer: 可选的 checkpointer 实例。当通过 ``langgraph dev``
            本地运行或部署到 LangGraph Platform 时，平台会自动注入
            自己的 checkpointer/store，此处应保持为 ``None`` 以避免与其
            冲突。仅在平台之外独立/本地使用编译该图时（如在测试或
            脚本中），才传入显式的 checkpointer（如 ``MemorySaver()``）。

    Returns:
        编译好的 StateGraph 实例。
    """
    builder = StateGraph(WorkflowState)

    # 节点
    builder.add_node("classify", classify_task)
    builder.add_node("research", _invoke_subgraph_as_node(build_problem_discovery_graph()))
    builder.add_node("data", _invoke_subgraph_as_node(build_decision_graph()))
    builder.add_node("produce", _invoke_subgraph_as_node(build_production_graph()))
    builder.add_node("qa", _invoke_subgraph_as_node(build_review_graph()))
    builder.add_node("human_confirm", human_confirm)

    # 边
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
    builder.add_edge("qa", "human_confirm")
    builder.add_edge("human_confirm", END)

    return builder.compile(checkpointer=checkpointer)


# 模块级别的已编译图实例，被 langgraph.json
# （`./src/codepilot/graphs/main_workflow.py:graph`）引用，以便 `langgraph dev` /
# `langgraph up` 无需自定义服务器即可发现并提供服务。此处不传入
# checkpointer：由平台自行注入。
graph = build_main_workflow()
