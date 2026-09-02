"""问题发现子图 — 阶段 1-2：明确问题。

流程（根据技术方案第 3.1 / 4.2 节的 fan-out-and-synthesis）：

    execute_research（推导种子查询）
        -> fan_out（为每个种子查询并行 Send 一个 `researcher` 任务）
        -> synthesize_results（将发现合并到 facts_ledger）

读/写：``facts_ledger``（由本子图拥有）。
使用的工具：``search_km``、``vector_memory``。
"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from codepilot.nodes import execute_research, researcher, synthesize_results
from codepilot.states.workflow_state import WorkflowState

# 并行 researcher 任务上限，防止 fan-out 爆炸
_MAX_PARALLEL_RESEARCHERS = 5


def _fan_out(state: WorkflowState) -> list[Send] | str:
    """为每个种子查询分发一个 ``researcher`` 任务以并行执行。

    LangGraph 会将空的 ``Send`` 列表视为“无出边分支”，这会导致运行提前
    终止而不会到达 ``synthesize``。如果未产生任何种子查询，则直接
    路由到 ``synthesize``。
    """
    queries = state.get("research_queries") or []
    if not queries:
        return "synthesize"
    # 限制并行任务数量，超出上限的截断
    capped = queries[:_MAX_PARALLEL_RESEARCHERS]
    if len(queries) > _MAX_PARALLEL_RESEARCHERS:
        import logging
        logging.getLogger(__name__).warning(
            "_fan_out: capped parallel researchers from %d to %d",
            len(queries), _MAX_PARALLEL_RESEARCHERS,
        )
    return [Send("researcher", {"query": query}) for query in capped]


def build_problem_discovery_graph() -> CompiledStateGraph:
    """构建问题发现子图。

    Returns:
        编译好的 StateGraph 实例。
    """
    builder = StateGraph(WorkflowState)

    builder.add_node("execute_research", execute_research)
    builder.add_node("researcher", researcher)
    builder.add_node("synthesize", synthesize_results)

    builder.set_entry_point("execute_research")
    builder.add_conditional_edges(
        "execute_research",
        _fan_out,
        ["researcher", "synthesize"],
    )
    builder.add_edge("researcher", "synthesize")
    builder.add_edge("synthesize", END)

    # 子图不设置独立 checkpointer，由主图的 checkpointer 统一管理持久化，
    # 避免 checkpoint 嵌套冲突。
    return builder.compile(checkpointer=False)


# 模块级别的已编译子图实例，用于独立测试／组合到主工作流图中。
graph = build_problem_discovery_graph()
