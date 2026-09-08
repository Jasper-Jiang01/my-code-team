"""Checkpoint 工厂：平台注入 / SQLite 落盘 / PostgresSaver。

同步版 ``create_checkpointer`` 供本地脚本与同步测试使用；事件循环内的
服务（FastAPI ``astream`` 等）必须用 ``create_async_checkpointer``，否则
同步 ``SqliteSaver`` 会抛
``The SqliteSaver does not support async methods``。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from codepilot.core.config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_DB = "postgresql://user:password@localhost:5432/codepilot"
_BACKEND_ROOT = Path(__file__).resolve().parents[3]

# PostgresSaver.from_conn_string 返回的 context manager 需进程级持有，
# 防止 GC 触发 GeneratorExit 关闭底层连接（见 _create_postgres_saver）。
_RETAINED_CMS: list[object] = []


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

            # 新版 from_conn_string 多为 context manager；长生命周期进程
            # 用 __enter__ 取出 saver。优先 ConnectionPool（若已安装）。
            saver: object | None = None
            try:
                from psycopg_pool import ConnectionPool

                pool = ConnectionPool(
                    conninfo=postgres_url,
                    kwargs={"autocommit": True, "prepare_threshold": 0},
                )
                saver = PostgresSaver(pool)
            except Exception:  # noqa: BLE001 - 未安装 psycopg_pool / 池构造失败时降级
                cm = PostgresSaver.from_conn_string(postgres_url)
                saver = cm.__enter__() if hasattr(cm, "__enter__") else cm
                # 必须持有 context manager 引用：生成器 frame 持有 psycopg 连接，
                # 若随局部变量被 GC，GeneratorExit 会关闭底层连接，首个
                # checkpoint 操作即失败（且外层 except 会静默降级 SQLite 掩盖问题）。
                _RETAINED_CMS.append(cm)

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
            # 启用 WAL 模式，提升多线程/多进程并发读写性能
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            logger.info("using SqliteSaver at %s (WAL mode)", path)
            return SqliteSaver(conn)
        except Exception:
            logger.exception("SqliteSaver unavailable, falling back to MemorySaver")

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


async def create_async_checkpointer(explicit: object | None = None) -> object | None:
    """返回可用于 ``astream`` / ``ainvoke`` 的异步 checkpointer。

    与 :func:`create_checkpointer` 同一套选择逻辑，但落盘实现换成
    ``AsyncSqliteSaver`` / ``AsyncPostgresSaver``（均需额外的异步驱动包）。
    """
    if explicit is not None:
        return None if explicit is False else explicit
    if _is_langgraph_platform():
        return None

    backend = (settings.checkpoint_backend or "auto").strip().lower()
    postgres_url = _postgres_url()
    if backend in {"postgres", "auto"} and postgres_url:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg_pool import AsyncConnectionPool

            # open=False 避免构造时在错误的事件循环上注册；随后显式 open。
            pool = AsyncConnectionPool(
                conninfo=postgres_url,
                kwargs={"autocommit": True, "prepare_threshold": 0},
                open=False,
            )
            await pool.open()
            saver = AsyncPostgresSaver(pool)
            await saver.setup()
            logger.info("using AsyncPostgresSaver")
            return saver
        except Exception:
            logger.exception("AsyncPostgresSaver unavailable, falling back")

    if backend in {"sqlite", "auto", "postgres"}:
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            path = _sqlite_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(str(path))
            try:
                # 启用 WAL 模式，提升并发读写性能
                async with conn.execute("PRAGMA journal_mode=WAL") as cursor:
                    await cursor.fetchone()
                await conn.execute("PRAGMA busy_timeout=5000")
            except Exception:
                await conn.close()
                raise
            logger.info("using AsyncSqliteSaver at %s (WAL mode)", path)
            return AsyncSqliteSaver(conn)
        except Exception:
            logger.exception("AsyncSqliteSaver unavailable, falling back to MemorySaver")

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()
