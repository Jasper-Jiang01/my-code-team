"""根据分类结果将任务路由到对应的子图。"""

from codepilot.core.intent_router import resolve_intent
from codepilot.states.workflow_state import WorkflowState


def route_task(state: WorkflowState) -> str:
    """根据当前 next_step 返回目标节点名称。

    Args:
        state: 当前的工作流状态。

    Returns:
        用于条件边路由的目标节点名称。
    """
    return state.get("next_step") if state.get("next_step") in {"research", "data", "produce", "qa"} else "research"


def route_after_research(state: WorkflowState) -> str:
    """研究结束后：完整 Demo 才继续数据闭环，否则结束。"""
    return "data" if resolve_intent(state).kind == "full" else "end"


def route_after_decision(state: WorkflowState) -> str:
    """决策结束后：完整 Demo 才进入生产，取数任务到此结束。"""
    return "produce" if resolve_intent(state).kind == "full" else "end"


def route_after_production(state: WorkflowState) -> str:
    """生产结束后：完整 Demo 或质检意图才进 ReviewGraph。"""
    kind = resolve_intent(state).kind
    return "qa" if kind in {"full", "review"} else "end"
