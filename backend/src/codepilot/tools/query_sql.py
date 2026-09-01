"""用于数据检索的 SQL 查询工具。"""

from typing import Any

from langchain_core.tools import tool


class SQLQueryError(RuntimeError):
    """当 SQL 查询失败、超时或被安全检查拒绝时抛出。"""


_FORBIDDEN_KEYWORDS = ("DROP ", "DELETE ", "TRUNCATE ", "ALTER ", "UPDATE ", "INSERT ")


@tool
def query_sql(sql: str, timeout: int = 30) -> list[dict[str, Any]]:
    """对数据仓库执行一个只读 SQL 查询。

    Args:
        sql: SQL 查询字符串。必须是只读（SELECT）语句。
        timeout: 查询超时时间（秒）。

    Returns:
        行数据字典列表。

    Raises:
        ValueError: 当 SQL 未通过基本安全性验证时。
        SQLQueryError: 当对数据仓库执行失败或超时时。
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

    # TODO: 通过 SQLDatabaseToolkit / SQLAlchemy 引擎实现真实执行
    # （settings.database_url 或 settings.sql_query_endpoint）。将连接、
    # 超时和驱动错误包装为 SQLQueryError。
    return []
