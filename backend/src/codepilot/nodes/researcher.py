"""研究员工作节点 — 处理单个被分发的种子查询。

本节点是 ``ProblemDiscoveryGraph`` 中 ``fan_out`` 所分发的 ``Send`` 目标。
每次调用处理恰好一个种子查询，使用 ``search_km`` / ``vector_memory``
工具，并把**检索到的证据正文**写入 ``research_findings``。
无检索结果时不伪造事实条目。
"""

import logging
from typing import Any, TypedDict

from codepilot.states.entries import make_fact
from codepilot.tools import search_km, vector_memory

logger = logging.getLogger(__name__)


class ResearcherInput(TypedDict):
    """单次 ``Send("researcher", ...)`` 分发的输入载荷。"""

    query: str


def _as_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def researcher(payload: ResearcherInput) -> dict:
    """研究单个种子查询并产出带证据的事实条目。

    Args:
        payload: 包含单一 ``query`` 键的字典，由
            ``Send("researcher", {"query": q})`` 分发而来。

    Returns:
        包含 ``research_findings`` 列表的字典；无证据时为空列表。
    """
    query = payload.get("query", "")
    if not query:
        return {"research_findings": []}

    try:
        km_results = _as_records(search_km.invoke({"query": query}))
    except Exception:  # noqa: BLE001 - 工具调用失败不得中断 fan-out 分支
        logger.exception("researcher: search_km failed for query=%r", query)
        km_results = []

    try:
        memory_results = _as_records(
            vector_memory.invoke(
                {"action": "search", "collection": "project_memory", "query": query}
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("researcher: vector_memory search failed for query=%r", query)
        memory_results = []

    findings = []
    for item in km_results:
        snippet = str(item.get("snippet") or item.get("content") or "")
        title = str(item.get("title") or "")
        if not snippet and not title:
            continue
        findings.append(
            make_fact(
                source=str(item.get("source") or "search_km"),
                metric=query,
                definition=title,
                value=snippet or title,
                url=str(item.get("url") or ""),
                snippet=snippet,
            )
        )
    for item in memory_results:
        snippet = str(item.get("snippet") or item.get("value") or item.get("content") or "")
        if not snippet:
            continue
        findings.append(
            make_fact(
                source=str(item.get("source") or "vector_memory"),
                metric=query,
                definition=str(item.get("metric") or item.get("title") or "project_memory"),
                value=snippet,
                url=str(item.get("url") or ""),
                snippet=snippet,
            )
        )

    if not findings:
        logger.info("researcher: no evidence for query=%r, skipping empty fact", query)
    return {"research_findings": findings}
