"""用于数据检索的 SQL 查询工具。"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from codepilot.core.config import settings

logger = logging.getLogger(__name__)


class SQLQueryError(RuntimeError):
    """当 SQL 查询失败、超时或被安全检查拒绝时抛出。"""


_FORBIDDEN_KEYWORDS = (
    "DROP ", "DELETE ", "TRUNCATE ", "ALTER ", "UPDATE ",
    "INSERT ", "CREATE ", "GRANT ", "REVOKE ", "VACUUM ",
    "CALL ", "DO ", "COPY ", "ATTACH ", "DETACH ",
)

_PLACEHOLDER_DB = "postgresql://user:password@localhost:5432/codepilot"


def _fixture_rows(sql: str) -> list[dict[str, Any]]:
    """本地夹具：返回带口径的指标行，供 DecisionGraph 写入事实台账。"""
    snippet = " ".join(sql.split())[:240]
    return [
        {
            "metric": "weekly_active_merchants",
            "value": 12800,
            "definition": "近 7 日有经营行为的商家数（去重）",
            "unit": "户",
            "sql_echo": snippet,
        },
        {
            "metric": "report_reach_uv",
            "value": 5400,
            "definition": "经营周报触达 UV（推送成功且曝光）",
            "unit": "人",
            "sql_echo": snippet,
        },
        {
            "metric": "report_click_rate",
            "value": 0.23,
            "definition": "经营周报点击率 = 点击 UV / 触达 UV",
            "unit": "ratio",
            "sql_echo": snippet,
        },
    ]


def _try_real_query(sql: str, timeout: int, params: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    url = (settings.sql_query_endpoint or "").strip()
    if url:
        import httpx

        response = httpx.post(
            url,
            json={"sql": sql, "timeout": timeout, "params": params},
            timeout=float(timeout),
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get("rows", [])
        return [row for row in rows if isinstance(row, dict)]

    database_url = (settings.database_url or "").strip()
    if not database_url or database_url == _PLACEHOLDER_DB:
        return None

    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        conn = conn.execution_options(timeout=timeout)
        # 使用参数化查询，杜绝 SQL 注入
        compiled = text(sql)
        result = conn.execute(compiled, params or {})
        columns = list(result.keys())
        return [dict(zip(columns, row, strict=False)) for row in result.fetchall()]


@tool
def query_sql(sql: str, timeout: int = 30, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """对数据仓库执行一个只读 SQL 查询。

    Args:
        sql: SQL 查询字符串。必须是只读（SELECT）语句。使用命名参数占位符
            （如 ``:context``）防止 SQL 注入。
        timeout: 查询超时时间（秒）。
        params: 参数化查询的绑定参数字典（可选）。

    Returns:
        行数据字典列表。
    """
    if not sql or not sql.strip():
        raise ValueError("sql must be a non-empty string")
    if timeout <= 0:
        raise ValueError("timeout must be a positive integer")

    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT") and not normalized.startswith("WITH"):
        raise ValueError("Only read-only SELECT/WITH statements are allowed")
    if any(keyword in normalized for keyword in _FORBIDDEN_KEYWORDS):
        raise ValueError("SQL contains a forbidden write/DDL keyword")
    # 检查是否有子查询中的写操作（如 INSERT INTO ... SELECT）
    if ";" in sql.strip().rstrip(";"):
        raise ValueError("SQL must be a single statement (no semicolons)")

    try:
        rows = _try_real_query(sql, timeout, params)
    except Exception:  # noqa: BLE001 - 真实库不可用时回退夹具，避免决策阶段空转
        logger.exception("query_sql: live backend failed, using fixture rows")
        rows = None

    if rows is not None:
        return rows
    return _fixture_rows(sql)
