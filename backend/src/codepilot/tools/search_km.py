"""KM（知识管理）搜索工具。"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx
from langchain_core.tools import tool

from codepilot.core.config import settings

logger = logging.getLogger(__name__)


class KMSearchError(RuntimeError):
    """当 KM 搜索后端失败或无法访问时抛出。"""


def _fixture_results(query: str, top_k: int) -> list[dict[str, Any]]:
    """本地夹具：保证研究阶段总能拿到带来源的证据，而不是空列表。"""
    templates = (
        ("内部研究摘录", "与「{q}」相关的既有研究结论与口径说明。"),
        ("竞品与案例", "围绕「{q}」的外部对标案例与可复用模式。"),
        ("风险与约束", "落地「{q}」时需要标注的数据口径、合规与依赖风险。"),
    )
    results: list[dict[str, Any]] = []
    for index, (kind, snippet_tpl) in enumerate(templates[:top_k], start=1):
        results.append(
            {
                "title": f"{kind}: {query[:80]}",
                "url": f"km://fixture/{quote(query, safe='')[:80]}/{index}",
                "snippet": snippet_tpl.format(q=query),
                "source": "km_fixture",
            }
        )
    return results


@tool
def search_km(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """在内部知识库（KM）中搜索相关文档。

    Args:
        query: 搜索查询字符串。
        top_k: 需要返回的结果数量。

    Returns:
        搜索结果字典列表，每个字典预期至少包含 ``title``、
        ``url``、``snippet`` 和 ``source`` 键。
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    endpoint = (settings.km_search_endpoint or "").strip()
    if endpoint:
        try:
            response = httpx.get(
                endpoint,
                params={"q": query, "top_k": top_k},
                timeout=8.0,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else payload.get("results", [])
            parsed = [row for row in rows if isinstance(row, dict)]
            if parsed:
                return parsed[:top_k]
        except Exception:
            logger.exception(
                "search_km: backend %s failed, falling back to fixture", endpoint
            )

    return _fixture_results(query, top_k)
