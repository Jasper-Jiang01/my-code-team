"""研究员工作节点 — 处理单个被分发的种子查询。

本节点是 ``ProblemDiscoveryGraph`` 中 ``fan_out`` 所分发的 ``Send`` 目标。
每次调用处理恰好一个种子查询，使用 ``search_km`` / ``vector_memory``
工具，并将结果追加到 ``research_findings``（通过该状态字段上的
``operator.add`` reducer 在并行分支之间自动合并）。
"""

import logging
from datetime import UTC, datetime
from typing import TypedDict

from codepilot.states.workflow_state import FactEntry
from codepilot.tools import search_km, vector_memory

logger = logging.getLogger(__name__)


class ResearcherInput(TypedDict):
    """单次 ``Send("researcher", ...)`` 分发的输入载荷。"""

    query: str


def researcher(payload: ResearcherInput) -> dict:
    """研究单个种子查询并产出一条事实条目。

    Args:
        payload: 包含单一 ``query`` 键的字典，由
            ``Send("researcher", {"query": q})`` 分发而来。

    Returns:
        包含 ``research_findings`` 列表的字典，最多包含一个
        :class:`FactEntry`，将被合并到父状态中。
    """
    query = payload.get("query", "")
    if not query:
        return {"research_findings": []}

    try:
        km_results = search_km.invoke({"query": query})
    except Exception:  # noqa: BLE001 - 工具调用失败不得中断 fan-out 分支
        logger.exception("researcher: search_km failed for query=%r", query)
        km_results = []

    try:
        memory_results = vector_memory.invoke(
            {"action": "search", "collection": "project_memory", "query": query}
        )
    except Exception:  # noqa: BLE001
        logger.exception("researcher: vector_memory search failed for query=%r", query)
        memory_results = []

    combined_sources = km_results if isinstance(km_results, list) else []
    combined_sources += memory_results if isinstance(memory_results, list) else []

    finding: FactEntry = {
        "source": "search_km+vector_memory" if combined_sources else "no_result",
        "metric": query,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return {"research_findings": [finding]}
