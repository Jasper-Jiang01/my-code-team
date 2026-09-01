"""Checkpoint 工厂：平台注入 / SQLite 落盘 / PostgresSaver。"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from codepilot.core.config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_DB = "postgresql://user:password@localhost:5432/codepilot"
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _is_langgraph_platform() -> bool:
    return bool(
        os.environ.get("LANGGRAPH_API")
        or os.environ.get("LANGGRAPH_RUNTIME")
        or os.environ.get("LANGSMITH_LANGGRAPH_API_VARIANT")
    )


def _sqlite_path() -> Path:
    configured = (settings.checkpoint_sqlite_path or "").strip()
    if configured:
        return Path(configured)
    return _BACKEND_ROOT / "checkpoints" / "main.sqlite"


def _postgres_url() -> str:
    url = (settings.database_url or "").strip()
    if not url or url == _PLACEHOLDER_DB:
        return ""
    return url


def create_checkpointer(explicit: object | None = None) -> object | None:
    """返回 checkpointer。

    - 显式传入则原样使用（包括 ``False`` 表示强制无 checkpointer）；
    - LangGraph Platform 运行时返回 None，由平台注入；
    - ``postgres`` 且 DATABASE_URL 可用时用 PostgresSaver；
    - 否则 SQLite 落到 ``checkpoints/main.sqlite``，再不行则 MemorySaver。
    """
    if explicit is not None:
        return None if explicit is False else explicit
    if _is_langgraph_platform():
        return None

    backend = (settings.checkpoint_backend or "auto").strip().lower()
    postgres_url = _postgres_url()
    if backend in {"postgres", "auto"} and postgres_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            saver = PostgresSaver.from_conn_string(postgres_url)
            setup = getattr(saver, "setup", None)
            if callable(setup):
                setup()
            logger.info("using PostgresSaver")
            return saver
        except Exception:
            logger.exception("PostgresSaver unavailable, falling back")

    if backend in {"sqlite", "auto", "postgres"}:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            path = _sqlite_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            logger.info("using SqliteSaver at %s", path)
            return SqliteSaver(conn)
        except Exception:
            logger.exception("SqliteSaver unavailable, falling back to MemorySaver")

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()
