"""质检阶段入口节点 — 初始化 ReviewGraph 工作字段。

作为 ``ReviewGraph`` 的入口节点，负责初始化评审阶段的内部工作字段，
随后控制权交给五岗位评委 fan-out 和三道门禁串联。
"""

import logging

from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)


def execute_qa(state: WorkflowState) -> dict:
    """初始化质检阶段的工作字段。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含初始化后的评审工作字段的字典。
    """
    demo = state.get("demo_artifact")
    if not demo:
        logger.warning("execute_qa: demo_artifact not produced yet, proceeding anyway")

    return {
        "review_round": 0,
        "function_gate": None,
        "visual_gate": None,
        "rehearsal_gate": None,
        "checkpoints": ["QA_INIT"],
    }
