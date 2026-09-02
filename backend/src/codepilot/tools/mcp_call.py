"""MCP 风格工具调用：有远端则走 JSON-RPC，否则路由到本地工具。"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.tools import tool

from codepilot.core.config import settings

logger = logging.getLogger(__name__)


class MCPCallError(RuntimeError):
    """当 MCP 调用失败时抛出。"""


def _local_catalog() -> list[dict[str, str]]:
    return [
        {"name": "python_repl", "description": "受限 Python 执行"},
        {"name": "deploy_demo", "description": "打包 Demo 产物"},
        {"name": "screenshot_diff", "description": "截图像素对比"},
        {"name": "search_km", "description": "内部知识检索"},
        {"name": "query_sql", "description": "只读 SQL 取数"},
        {"name": "vector_memory", "description": "项目向量记忆"},
        {"name": "pde_prototype", "description": "点评 PDE 页面原型图"},
    ]


def _dispatch_local(name: str, arguments: dict[str, Any]) -> Any:
    from codepilot.tools.deploy_demo import deploy_demo
    from codepilot.tools.pde_prototype import pde_prototype
    from codepilot.tools.python_repl import python_repl
    from codepilot.tools.query_sql import query_sql
    from codepilot.tools.screenshot_diff import screenshot_diff
    from codepilot.tools.search_km import search_km
    from codepilot.tools.vector_memory import vector_memory

    registry = {
        "python_repl": python_repl,
        "deploy_demo": deploy_demo,
        "screenshot_diff": screenshot_diff,
        "search_km": search_km,
        "query_sql": query_sql,
        "vector_memory": vector_memory,
        "pde_prototype": pde_prototype,
    }
    tool_obj = registry.get(name)
    if tool_obj is None:
        raise MCPCallError(f"unknown local MCP tool: {name}")
    return tool_obj.invoke(arguments)


@tool
def mcp_call(
    method: str,
    name: str = "",
    arguments: dict[str, Any] | None = None,
    server: str = "codepilot",
) -> dict[str, Any]:
    """调用 MCP 工具（JSON-RPC）。未配置远端时使用本地工具目录。

    Args:
        method: MCP 方法，常用 ``tools/list`` 或 ``tools/call``。
        name: ``tools/call`` 时的工具名。
        arguments: ``tools/call`` 时的参数。
        server: 逻辑服务器名（本地默认为 ``codepilot``）。
    """
    if not method or not method.strip():
        raise ValueError("method must be a non-empty string")

    endpoint = (settings.mcp_endpoint or "").strip()
    if endpoint:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {"name": name, "arguments": arguments or {}, "server": server},
        }
        try:
            response = httpx.post(endpoint, json=payload, timeout=15.0)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise MCPCallError(f"MCP endpoint failed: {exc}") from exc
        if isinstance(body, dict) and body.get("error"):
            raise MCPCallError(str(body["error"]))
        return body if isinstance(body, dict) else {"result": body}

    if method in {"tools/list", "list"}:
        return {"server": server, "tools": _local_catalog()}
    if method in {"tools/call", "call"}:
        if not name:
            raise ValueError("name is required for tools/call")
        result = _dispatch_local(name, arguments or {})
        return {"server": server, "name": name, "result": result}
    raise MCPCallError(f"unsupported MCP method: {method}")
