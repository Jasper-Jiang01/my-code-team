"""根据分类结果将任务路由到对应的子图。"""

from codepilot.states.workflow_state import WorkflowState


def route_task(state: WorkflowState) -> str:
    """根据当前 next_step 返回目标节点名称。

    Args:
        state: 当前的工作流状态。

    Returns:
        用于条件边路由的目标节点名称。
    """
    return state.get("next_step", "research")
