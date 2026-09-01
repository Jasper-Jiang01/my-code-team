"""将并行子 Agent 的结果汇总为统一输出。"""

import logging

from codepilot.core.memory_store import update_project_memory
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)


def synthesize_results(state: WorkflowState) -> dict:
    """将并行的 researcher 发现汇总到事实台账中。

    收集累积在 ``research_findings`` 中的每个查询发现（由并行的
    ``researcher`` 分支通过 ``Send`` 填充），将其合并到 ``facts_ledger``，
    并将其中的关键词持久化到项目记忆中（根据设计文档，仅限于
    ``ProblemDiscoveryGraph``）。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含更新后 ``facts_ledger`` 和完成 checkpoint 的字典。
    """
    findings = state.get("research_findings", [])

    if not findings:
        logger.info("synthesize_results: no research findings to synthesize")
        return {"checkpoints": ["research_done"]}

    keywords = [f["metric"] for f in findings if f.get("metric")]
    try:
        update_project_memory(keywords=keywords, research_index=findings)
    except Exception:  # noqa: BLE001 - 记忆持久化失败不得阻塞图的执行
        logger.exception("synthesize_results: failed to persist project memory")

    return {
        "facts_ledger": findings,
        "checkpoints": ["research_done"],
    }
