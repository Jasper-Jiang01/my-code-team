"""受限 Python REPL，供生产 BUILD 步执行代码。

安全模型：
1. AST 静态扫描拦截 import / eval / exec / dunder 访问等危险操作。
2. ``open`` 被沙箱版替代，使用 :meth:`pathlib.Path.is_relative_to`
   做目录归属判定（而非字符串前缀），杜绝相邻同前缀目录绕过。
3. 代码在受限子进程中执行，设置 CPU 时间、虚拟内存和墙钟超时上限，
   防止无限循环永久占住工作流节点。
"""

from __future__ import annotations

import ast
import json
import logging
import pickle
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from codepilot.core.config import settings

logger = logging.getLogger(__name__)

# 禁止调用的函数/属性名
_FORBIDDEN_NAMES = frozenset({
    "__import__",
    "eval",
    "exec",
    "compile",
    "globals",
    "locals",
    "breakpoint",
    "exit",
    "quit",
    "getattr",
    "setattr",
    "delattr",
    "vars",
    "dir",
    "open",  # open 被沙箱版替代
})

# 禁止出现的属性名（以 __ 包裹的 dunder）
_FORBIDDEN_ATTRS = frozenset({
    "__builtins__",
    "__subclasses__",
    "__bases__",
    "__mro__",
    "__class__",
    "__globals__",
    "__code__",
    "__func__",
    "__self__",
    "__dict__",
    "__module__",
    "__qualname__",
})

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

# 子进程资源上限
_CPU_TIME_SEC = 10      # 用户态 + 内核态 CPU 时间上限
_MEM_LIMIT_MB = 512     # 虚拟内存上限
_WALL_TIMEOUT_SEC = 30  # 墙钟超时（含 I/O 等待），防止 sleep/死循环


class PythonREPLError(RuntimeError):
    """当代码被拒绝或执行失败时抛出。"""


class _SafetyChecker(ast.NodeVisitor):
    """AST 遍历器：检测 import / eval / exec / dunder 访问等危险操作。"""

    def visit_Import(self, node: ast.Import) -> None:
        raise PythonREPLError("import statements are not allowed")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        raise PythonREPLError("from ... import statements are not allowed")

    def visit_Call(self, node: ast.Call) -> None:
        # 检查直接调用 eval / exec 等
        if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_NAMES:
            raise PythonREPLError(f"call to '{node.func.id}' is not allowed")
        if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_NAMES:
            raise PythonREPLError(f"call to '{node.func.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # 阻止访问 __subclasses__ / __globals__ 等
        if node.attr in _FORBIDDEN_ATTRS or (
            node.attr.startswith("__") and node.attr.endswith("__")
        ):
            raise PythonREPLError(f"access to '{node.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            raise PythonREPLError(f"use of '{node.id}' is not allowed")
        self.generic_visit(node)


def _check_code_safety(code: str) -> None:
    """解析 AST 并检查是否有危险操作。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise PythonREPLError(f"syntax error: {exc}") from exc
    _SafetyChecker().visit(tree)


def _safe_open_factory(workdir: Path):
    workdir = workdir.resolve()

    def _safe_open(path: str, mode: str = "r", *args: Any, **kwargs: Any):
        if any(flag in mode for flag in ("x",)):
            raise PythonREPLError("open mode not allowed")
        target = Path(path)
        resolved = (workdir / target).resolve() if not target.is_absolute() else target.resolve()
        # 使用 is_relative_to 做目录归属判定，杜绝相邻同前缀目录绕过
        if not resolved.is_relative_to(workdir):
            raise PythonREPLError(f"refusing to access path outside workdir: {resolved}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return open(resolved, mode, *args, **kwargs)  # noqa: SIM115

    return _safe_open


# ---------------------------------------------------------------------------
# 子进程执行
# ---------------------------------------------------------------------------

# 子进程引导脚本：设置资源上限后执行用户代码，结果通过 pickle 回传。
_BOOTSTRAP_TEMPLATE = r"""
import json, pickle, resource, sys, types

workdir = {workdir!r}
code = {code!r}
allowed_builtins = {allowed_builtins!r}
result_path = {result_path!r}

# 设置 CPU / 内存上限
resource.setrlimit(resource.RLIMIT_CPU, ({cpu_sec}, {cpu_sec}))
resource.setrlimit(resource.RLIMIT_AS, ({mem_bytes}, {mem_bytes}))

# 重建沙箱 builtins
def _safe_open(path, mode="r", *args, **kwargs):
    from pathlib import Path
    workdir_path = Path(workdir)
    target = Path(path)
    resolved = (workdir_path / target).resolve() if not target.is_absolute() else target.resolve()
    if not resolved.is_relative_to(workdir_path):
        raise RuntimeError(f"refusing to access path outside workdir: {{resolved}}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return open(resolved, mode, *args, **kwargs)

sandbox_builtins = dict(allowed_builtins)
sandbox_builtins["open"] = _safe_open
sandbox_globals = {{"__builtins__": sandbox_builtins, "result": None, "WORKDIR": workdir}}

import io
from contextlib import redirect_stdout, redirect_stderr

stdout_buf = io.StringIO()
stderr_buf = io.StringIO()
error_info = None
try:
    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
        exec(code, sandbox_globals, sandbox_globals)
except Exception as exc:
    error_info = repr(exc)

result = sandbox_globals.get("result")
payload = {{
    "ok": error_info is None,
    "stdout": stdout_buf.getvalue(),
    "stderr": stderr_buf.getvalue() or (error_info or ""),
    "result": result if isinstance(result, (str, int, float, bool, list, dict)) or result is None else repr(result),
}}
with open(result_path, "wb") as f:
    pickle.dump(payload, f)
"""


def _run_in_subprocess(code: str, workdir: Path) -> dict[str, Any]:
    """在受限子进程中执行用户代码。"""
    with tempfile.NamedTemporaryFile(
        dir=workdir, suffix=".pkl", delete=False
    ) as tmp:
        result_path = tmp.name

    bootstrap = _BOOTSTRAP_TEMPLATE.format(
        workdir=str(workdir),
        code=code,
        allowed_builtins=_ALLOWED_BUILTINS,
        result_path=result_path,
        cpu_sec=_CPU_TIME_SEC,
        mem_bytes=_MEM_LIMIT_MB * 1024 * 1024,
    )

    try:
        completed = subprocess.run(
            [sys.executable, "-c", bootstrap],
            capture_output=True,
            text=True,
            timeout=_WALL_TIMEOUT_SEC,
            cwd=str(workdir),
        )
        if completed.returncode != 0:
            # 子进程崩溃（CPU 超时会发 SIGXCPU）
            stderr_tail = (completed.stderr or "")[-500:]
            return {
                "ok": False,
                "stdout": completed.stdout,
                "stderr": f"subprocess exited with code {completed.returncode}: {stderr_tail}",
                "result": None,
            }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"execution timed out after {_WALL_TIMEOUT_SEC}s",
            "result": None,
        }

    try:
        with open(result_path, "rb") as f:
            payload: dict[str, Any] = pickle.load(f)
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.exception("python_repl: failed to read subprocess result")
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"failed to read result: {exc}",
            "result": None,
        }
    finally:
        Path(result_path).unlink(missing_ok=True)


@tool
def python_repl(code: str, workdir: str = "") -> dict[str, Any]:
    """在受限环境中执行 Python 代码（禁止 import/eval；open 仅限产物目录）。

    代码在受限子进程中执行，设置了 CPU 时间、虚拟内存和墙钟超时上限，
    防止无限循环永久占住工作流节点。

    Args:
        code: 要执行的 Python 源码。可用 ``result`` 变量作为返回值。
        workdir: 可选工作目录。默认为 ``settings.artifacts_dir``。

    Returns:
        包含 ``ok``、``stdout``、``stderr``、``result`` 的字典。
    """
    if not code or not code.strip():
        raise ValueError("code must be a non-empty string")

    _check_code_safety(code)

    root = Path(workdir).resolve() if workdir.strip() else Path(settings.artifacts_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    return _run_in_subprocess(code, root)
