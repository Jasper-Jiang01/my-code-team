"""决策子图 — 阶段 3-4：data测算 与 指标与方案。

流程（根据技术方案第 3.1 / 4.3 节的对抗式验证）：

    execute_data（指标验证与规模估算）
        -> producer（起草方案）
        -> critic（红军挑战）
        -> needs_fix？ -> 回到 producer
        -> pass？      -> judge（锁定 spec + evidence）

读/写：``spec`` / ``evidence``（由本子图拥有）。
使用的工具：``query_sql``（通过 ``data`` Agent Harness）。
循环契约：``critic`` 在有限轮数后会强制通过
（参见 ``nodes.critic._MAX_ROUNDS``），以保证循环一定收敛。
"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.nodes import critic, execute_data, judge, producer
from codepilot.states.workflow_state import WorkflowState


def _needs_fix(state: WorkflowState) -> str:
    """根据 critic 的裁决进行路由：回到循环或进入 judge。"""
    return "fix" if state.get("decision_verdict") == "needs_fix" else "pass"


def build_decision_graph() -> CompiledStateGraph:
    """构建决策子图。

    Returns:
        编译好的 StateGraph 实例。
    """
    builder = StateGraph(WorkflowState)

    builder.add_node("execute_data", execute_data)
    builder.add_node("producer", producer)
    builder.add_node("critic", critic)
    builder.add_node("judge", judge)

    builder.set_entry_point("execute_data")
    builder.add_edge("execute_data", "producer")
    builder.add_edge("producer", "critic")
    builder.add_conditional_edges(
        "critic",
        _needs_fix,
        {"fix": "producer", "pass": "judge"},
    )
    builder.add_edge("judge", END)

    return builder.compile()


# 模块级别的已编译子图实例，用于独立测试／组合到主工作流图中。
graph = build_decision_graph()
