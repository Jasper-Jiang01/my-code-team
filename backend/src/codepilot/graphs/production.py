"""生产子图 — 阶段 5-6：规格设计与高保真 Demo。

流程（根据技术方案第 3.1 / 5 节的静态六步子流程）：

    execute_produce（初始化）
        -> 01 explore (EXPLORE 需求增量)
        -> 02 generate (GENERATE 设计草稿)
        -> 03 guard (GUARD DP 审核)
            -> 未通过且未达轮次上限：回到 generate
            -> 通过或强制继续：04 build
        -> 05 compare (COMPARE 视觉还原)
        -> 06 verify (VERIFY QA 验收，汇总 demo_artifact)
"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.core.intent_router import resolve_intent
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

_MAX_GUARD_ROUNDS = 2


def _after_init(state: WorkflowState) -> str:
    """按意图跳过用不到的生产步骤：出原型不必先 EXPLORE，写代码可从 BUILD 起。"""
    intent = resolve_intent(state)
    if intent.kind == "code" and "pde_prototype" not in intent.tools:
        return "build"
    if intent.kind in {"prototype", "spec", "code"}:
        return "generate"
    return "explore"


def _after_generate(state: WorkflowState) -> str:
    """原型 / 需求文档在 GENERATE 后收口；写代码跳过 GUARD 直接 BUILD。"""
    kind = resolve_intent(state).kind
    if kind in {"prototype", "spec"}:
        return "verify"
    if kind == "code":
        return "build"
    return "guard"


def _after_build(state: WorkflowState) -> str:
    """没有视觉比对工具时跳过 COMPARE。"""
    if "screenshot_diff" in resolve_intent(state).tools:
        return "compare"
    return "verify"


def _after_guard(state: WorkflowState) -> str:
    """GUARD 未通过时回到 GENERATE；达到轮次上限后强制进入 BUILD。"""
    audit = state.get("design_audit") or {}
    round_count = int(state.get("production_guard_round") or 0)
    if audit.get("approved", False):
        return "build"
    if round_count >= _MAX_GUARD_ROUNDS:
        return "build"
    return "generate"


def build_production_graph() -> CompiledStateGraph:
    """构建生产子图（静态六步子流程，GUARD 可回环）。"""
    builder = StateGraph(WorkflowState)

    builder.add_node("execute_produce", execute_produce)
    builder.add_node("explore", explore)
    builder.add_node("generate", generate)
    builder.add_node("guard", guard)
    builder.add_node("build", build)
    builder.add_node("compare", compare)
    builder.add_node("verify", verify)

    builder.set_entry_point("execute_produce")
    builder.add_conditional_edges(
        "execute_produce",
        _after_init,
        {"explore": "explore", "generate": "generate", "build": "build"},
    )
    builder.add_edge("explore", "generate")
    builder.add_conditional_edges(
        "generate",
        _after_generate,
        {"guard": "guard", "build": "build", "verify": "verify"},
    )
    builder.add_conditional_edges(
        "guard",
        _after_guard,
        {"build": "build", "generate": "generate"},
    )
    builder.add_conditional_edges(
        "build",
        _after_build,
        {"compare": "compare", "verify": "verify"},
    )
    builder.add_edge("compare", "verify")
    builder.add_edge("verify", END)

    # checkpointer=None：继承父图检查点（与主图同一 thread 持久化）。
    return builder.compile(checkpointer=None)


graph = build_production_graph()
