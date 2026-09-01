"""KM（知识管理）搜索工具。"""

from typing import Any

from langchain_core.tools import tool


class KMSearchError(RuntimeError):
    """当 KM 搜索后端失败或无法访问时抛出。"""


@tool
def search_km(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """在内部知识库（KM）中搜索相关文档。

    Args:
        query: 搜索查询字符串。
        top_k: 需要返回的结果数量。

    Returns:
        搜索结果字典列表，每个字典预期至少包含 ``title``、
        ``url``、``snippet`` 和 ``source`` 键。

    Raises:
        KMSearchError: 当 KM 后端请求失败时。
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    # TODO: 实现真实的 KM API 集成（settings.km_search_endpoint）。
    # 将网络/解析失败包装为 KMSearchError，以便调用方可以捕获
    # 单一的已知异常类型。
    return []
