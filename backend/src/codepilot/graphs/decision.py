"""决策子图 — 阶段 3-4：data测算 与 指标与方案。

流程：
    execute_data
        -> fan_out 3 个 candidate_producer（生成过滤）
        -> filter_candidates
        -> tournament 两两比较
        -> critic（红军挑战）
        -> needs_fix？ -> producer 修订胜者
        -> pass？      -> judge（锁定 spec + evidence）
"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.nodes import critic, execute_data, judge, producer
from codepilot.nodes.tournament import (
    candidate_producer,
    fan_out_candidates,
    filter_candidates,
    tournament,
)
from codepilot.states.workflow_state import WorkflowState

_MAX_DECISION_FIX_ROUNDS = 2


def _needs_fix(state: WorkflowState) -> str:
    """根据 critic 的裁决进行路由：回到循环或进入 judge。

    当 fix 轮次超过上限时强制进入 judge，防止无限循环。
    """
    round_count = int(state.get("decision_round") or 0)
    if round_count >= _MAX_DECISION_FIX_ROUNDS:
        return "pass"
    return "fix" if state.get("decision_verdict") == "needs_fix" else "pass"


def build_decision_graph() -> CompiledStateGraph:
    """构建决策子图。"""
    builder = StateGraph(WorkflowState)

    builder.add_node("execute_data", execute_data)
    builder.add_node("candidate_producer", candidate_producer)
    builder.add_node("filter_candidates", filter_candidates)
    builder.add_node("tournament", tournament)
    builder.add_node("producer", producer)
    builder.add_node("critic", critic)
    builder.add_node("judge", judge)

    builder.set_entry_point("execute_data")
    builder.add_conditional_edges(
        "execute_data",
        fan_out_candidates,
        ["candidate_producer"],
    )
    builder.add_edge("candidate_producer", "filter_candidates")
    builder.add_edge("filter_candidates", "tournament")
    builder.add_edge("tournament", "critic")
    builder.add_conditional_edges(
        "critic",
        _needs_fix,
        {"fix": "producer", "pass": "judge"},
    )
    builder.add_edge("producer", "critic")
    builder.add_edge("judge", END)

    # checkpointer=None：继承父图检查点（与主图同一 thread 持久化）。
    return builder.compile(checkpointer=None)


graph = build_decision_graph()
