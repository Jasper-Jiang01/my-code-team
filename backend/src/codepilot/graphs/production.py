"""生产子图 — 阶段 5-6：规格设计与高保真 Demo。

流程（根据技术方案第 3.1 / 5 节的静态六步子流程）：

    execute_produce（初始化）
        -> 01 explore (EXPLORE 需求增量)
        -> 02 generate (GENERATE 设计草稿)
        -> 03 guard (GUARD DP 审核)
        -> 04 build (BUILD 开发实现)
        -> 05 compare (COMPARE 视觉还原)
        -> 06 verify (VERIFY QA 验收，汇总 demo_artifact)

这是一个**不可缺步的静态子流程**：每一步严格按顺序执行，
通过 Checkpoint 证明真实完成。

读/写：``demo_artifact``（由本子图拥有）。
使用的工具：``deploy_demo``、``screenshot_diff``（通过 ``design`` Agent Harness）。
"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.nodes import (
    build,
    compare,
    execute_produce,
    explore,
    generate,
    guard,
    verify,
)
from codepilot.states.workflow_state import WorkflowState


def build_production_graph() -> CompiledStateGraph:
    """构建生产子图（静态六步子流程）。

    Returns:
        编译好的 StateGraph 实例。
    """
    builder = StateGraph(WorkflowState)

    builder.add_node("execute_produce", execute_produce)
    builder.add_node("explore", explore)
    builder.add_node("generate", generate)
    builder.add_node("guard", guard)
    builder.add_node("build", build)
    builder.add_node("compare", compare)
    builder.add_node("verify", verify)

    builder.set_entry_point("execute_produce")
    builder.add_edge("execute_produce", "explore")
    builder.add_edge("explore", "generate")
    builder.add_edge("generate", "guard")
    builder.add_edge("guard", "build")
    builder.add_edge("build", "compare")
    builder.add_edge("compare", "verify")
    builder.add_edge("verify", END)

    return builder.compile()


# 模块级别的已编译子图实例，用于独立测试／组合到主工作流图中。
graph = build_production_graph()
