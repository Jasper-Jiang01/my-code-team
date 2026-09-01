"""SQL query tool for data retrieval."""

from typing import Any


def query_sql(sql: str, timeout: int = 30) -> list[dict[str, Any]]:
    """Execute a SQL query against the data warehouse.

    Args:
        sql: The SQL query string.
        timeout: Query timeout in seconds.

    Returns:
        A list of row dicts.
    """
    # TODO: Implement SQL execution with safety checks
    return []
