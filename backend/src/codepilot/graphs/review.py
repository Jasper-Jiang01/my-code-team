"""评审子图 — 阶段 7-9：红蓝对抗、质检、答辩。

流程（根据技术方案第 3.1 / 7 节）：

    execute_qa（初始化）
        -> review_fan_out: Send(panel, panel_ref) 五岗位评委并行评审
        -> function_gate（功能门）
        -> visual_gate（视觉门）
        -> rehearsal_gate（演示门）
        -> loop_condition:
            "fix"  -> fix_agent（证据驱动修复）-> 回到 function_gate
            "done" -> finalize_review（汇总 qa_report）

读/写：``issues_ledger`` / ``qa_report``（由本子图拥有）。
使用的工具：``screenshot_diff``、``deploy_demo``（通过 ``qa`` Agent Harness）。
"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.nodes import (
    execute_qa,
    finalize_review,
    fix_agent,
    function_gate,
    loop_condition,
    panel,
    rehearsal_gate,
    review_fan_out,
    visual_gate,
)
from codepilot.states.workflow_state import WorkflowState


def build_review_graph() -> CompiledStateGraph:
    """构建评审子图。

    Returns:
        编译好的 StateGraph 实例。
    """
    builder = StateGraph(WorkflowState)

    builder.add_node("execute_qa", execute_qa)
    builder.add_node("panel", panel)
    builder.add_node("function_gate", function_gate)
    builder.add_node("visual_gate", visual_gate)
    builder.add_node("rehearsal_gate", rehearsal_gate)
    builder.add_node("fix_agent", fix_agent)
    builder.add_node("finalize_review", finalize_review)

    builder.set_entry_point("execute_qa")
    # execute_qa -> review_fan_out（Send fan-out 到 panel，或修复回环跳过评委）
    builder.add_conditional_edges(
        "execute_qa",
        review_fan_out,
        ["panel", "function_gate"],
    )
    builder.add_edge("panel", "function_gate")
    builder.add_edge("function_gate", "visual_gate")
    builder.add_edge("visual_gate", "rehearsal_gate")
    builder.add_conditional_edges(
        "rehearsal_gate",
        loop_condition,
        {"fix": "fix_agent", "done": "finalize_review"},
    )
    builder.add_edge("fix_agent", "function_gate")
    builder.add_edge("finalize_review", END)

    return builder.compile()


# 模块级别的已编译子图实例，用于独立测试／组合到主工作流图中。
graph = build_review_graph()
