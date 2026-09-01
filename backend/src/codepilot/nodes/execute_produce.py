"""生产阶段入口节点 — 初始化静态六步子流程。

作为 ``ProductionGraph`` 的入口节点，负责：
1. 检查上游 ``spec`` / ``evidence`` 是否已就绪；
2. 初始化 ``production_step`` 等工作字段；
3. 将控制权交给后续的六步静态节点（explore -> generate -> guard
   -> build -> compare -> verify）。
"""

import logging

from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)


def execute_produce(state: WorkflowState) -> dict:
    """初始化生产阶段的工作字段。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含初始化后的生产工作字段的字典。
    """
    spec = state.get("spec")
    if not spec:
        logger.warning("execute_produce: spec not locked yet, proceeding with empty spec")

    return {
        "production_step": 0,
        "design_draft": None,
        "design_audit": None,
        "build_artifact": None,
        "visual_compare": None,
        "checkpoints": ["PRODUCE_INIT"],
    }
