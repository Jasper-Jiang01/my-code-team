"""用于项目与 Agent 记忆存储的向量记忆工具。"""

from typing import Any, Literal

from langchain_core.tools import tool


class VectorMemoryError(RuntimeError):
    """当向量记忆后端失败时抛出。"""


_VALID_ACTIONS = ("add", "search")


@tool
def vector_memory(
    action: Literal["add", "search"],
    collection: str,
    data: dict[str, Any] | None = None,
    query: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]] | bool:
    """与向量记忆存储进行交互。

    Args:
        action: ``"add"`` 表示插入一条记录，``"search"`` 表示查询。
        collection: 记忆集合名称（如 ``project_memory``、
            ``agent_memory.research``）。
        data: 需要添加的数据（当 ``action="add"`` 时必需）。
        query: 查询字符串（当 ``action="search"`` 时必需）。
        top_k: 搜索返回的结果数量。

    Returns:
        当 ``action="search"`` 时返回搜索结果字典列表，当
        ``action="add"`` 时返回表示成功与否的布尔值。

    Raises:
        ValueError: 当参数不符合所请求操作的要求时。
        VectorMemoryError: 当底层向量存储操作失败时。
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be one of {_VALID_ACTIONS}")
    if not collection or not collection.strip():
        raise ValueError("collection must be a non-empty string")
    if action == "add" and not data:
        raise ValueError("data is required when action='add'")
    if action == "search" and not query:
        raise ValueError("query is required when action='search'")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    # TODO: 实现 Chroma/Pinecone 向量存储集成
    # （settings.vector_store_url）。将连接/索引错误包装为
    # VectorMemoryError。
    if action == "search":
        return []
    return True
