"""受限 Python REPL，供生产 BUILD 步执行代码。"""

from __future__ import annotations

import io
import logging
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from codepilot.core.config import settings

logger = logging.getLogger(__name__)

_FORBIDDEN = re.compile(
    r"\b(import|__import__|eval|exec|compile|classmethod|staticmethod|globals|locals|breakpoint)\b"
    r"|__\w+__",
)

_ALLOWED_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "repr": repr,
    "round": round,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "zip": zip,
}


class PythonREPLError(RuntimeError):
    """当代码被拒绝或执行失败时抛出。"""


def _safe_open_factory(workdir: Path):
    workdir = workdir.resolve()

    def _safe_open(path: str, mode: str = "r", *args: Any, **kwargs: Any):
        if any(flag in mode for flag in ("x",)):
            raise PythonREPLError("open mode not allowed")
        target = Path(path)
        resolved = (workdir / target).resolve() if not target.is_absolute() else target.resolve()
        if not str(resolved).startswith(str(workdir)):
            raise PythonREPLError(f"refusing to access path outside workdir: {resolved}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return open(resolved, mode, *args, **kwargs)  # noqa: SIM115

    return _safe_open


@tool
def python_repl(code: str, workdir: str = "") -> dict[str, Any]:
    """在受限环境中执行 Python 代码（禁止 import/eval；open 仅限产物目录）。

    Args:
        code: 要执行的 Python 源码。可用 ``result`` 变量作为返回值。
        workdir: 可选工作目录。默认为 ``settings.artifacts_dir``。

    Returns:
        包含 ``ok``、``stdout``、``stderr``、``result`` 的字典。
    """
    if not code or not code.strip():
        raise ValueError("code must be a non-empty string")
    if _FORBIDDEN.search(code):
        raise PythonREPLError("code contains disallowed names (import/eval/exec/__dunder__)")


    root = Path(workdir).resolve() if workdir.strip() else Path(settings.artifacts_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    sandbox_globals: dict[str, Any] = {
        "__builtins__": {**_ALLOWED_BUILTINS, "open": _safe_open_factory(root)},
        "result": None,
        "WORKDIR": str(root),
    }
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, sandbox_globals, sandbox_globals)  # noqa: S102 - intentional sandboxed exec
    except Exception as exc:  # noqa: BLE001
        logger.exception("python_repl: execution failed")
        return {
            "ok": False,
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue() or str(exc),
            "result": None,
        }

    result = sandbox_globals.get("result")
    return {
        "ok": True,
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "result": result if isinstance(result, (str, int, float, bool, list, dict)) or result is None else repr(result),
    }
