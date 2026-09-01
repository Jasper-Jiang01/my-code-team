"""将并行子 Agent 的结果汇总为统一输出。"""

import logging

from codepilot.core.memory_store import update_agent_memory, update_project_memory
from codepilot.states.workflow_state import WorkflowState
from codepilot.tools import vector_memory

logger = logging.getLogger(__name__)


def synthesize_results(state: WorkflowState) -> dict:
    """将并行的 researcher 发现汇总到事实台账中。

    收集累积在 ``research_findings`` 中的每个查询发现（由并行的
    ``researcher`` 分支通过 ``Send`` 填充），将其合并到 ``facts_ledger``，
    并把关键词 / 证据索引写入项目记忆和向量记忆。
    """
    findings = [item for item in (state.get("research_findings") or []) if item.get("value") or item.get("snippet")]

    if not findings:
        logger.info("synthesize_results: no research findings to synthesize")
        return {"checkpoints": ["research_done"]}

    keywords = [item["metric"] for item in findings if item.get("metric")]
    try:
        update_project_memory(keywords=keywords, research_index=findings)
    except Exception:  # noqa: BLE001 - 记忆持久化失败不得阻塞图的执行
        logger.exception("synthesize_results: failed to persist project memory")

    try:
        update_agent_memory(
            "research_agent",
            keywords=keywords,
            last_fact_count=len(findings),
        )
    except Exception:  # noqa: BLE001
        logger.exception("synthesize_results: failed to persist agent memory")

    for fact in findings:
        try:
            vector_memory.invoke(
                {"action": "add", "collection": "project_memory", "data": dict(fact)}
            )
        except Exception:  # noqa: BLE001
            logger.exception("synthesize_results: failed to index fact in vector memory")

    return {
        "facts_ledger": findings,
        "checkpoints": ["research_done"],
    }
