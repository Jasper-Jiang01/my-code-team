"""两种基于文件的记忆存储的读/写辅助函数。

根据技术方案（第 3.2.4 节）：

- **项目记忆**（``memory/project_memory.json``）：一个单一的 JSON 文件，
  存储研究索引、关键词网络和历史判断。它仅*限于* ``ProblemDiscoveryGraph``
  使用，不对其他三个子图开放。
- **Agent 记忆**（``memory/agent_memory/<agent_name>.json``）：按 Agent 隔离的
  领域记忆。每个 Agent（research/data/design/qa/...）都拥有自己的
  命名空间文件，四个子图都可以读写自己 Agent 的命名空间。

这两种存储在第一阶段 MVP 中故意设计得非常简单，都是基于 JSON 文件的
键/值存储。它们并不能取代向量存储（``codepilot.tools.vector_memory``），
后者负责基于 embedding 的检索；这些辅助函数处理的是需要跨次运行持久化
的小型结构化事实。
"""

import json
import threading
from pathlib import Path
from typing import Any

# backend/ 根目录，即 src/codepilot/ 的上级目录
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_MEMORY_DIR = _BACKEND_ROOT / "memory"
_PROJECT_MEMORY_PATH = _MEMORY_DIR / "project_memory.json"
_AGENT_MEMORY_DIR = _MEMORY_DIR / "agent_memory"

_lock = threading.Lock()


class MemoryStoreError(RuntimeError):
    """当记忆文件无法读取或写入时抛出。"""


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return dict(default)
        return json.loads(content)
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryStoreError(f"Failed to read memory file {path}: {exc}") from exc


def _write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise MemoryStoreError(f"Failed to write memory file {path}: {exc}") from exc


# --------------------------------------------------------------------------
# 项目记忆 —— 仅限于 ProblemDiscoveryGraph 使用
# --------------------------------------------------------------------------

_PROJECT_MEMORY_DEFAULT: dict[str, Any] = {
    "project_name": "CodePilot",
    "description": "Multi-Agent Dynamic Workflow System",
    "keywords": [],
    "research_index": [],
    "data_metrics": [],
    "historical_judgments": [],
}


def load_project_memory() -> dict[str, Any]:
    """加载项目级记忆（研究索引、关键词等）。

    预期使用方：仅 ``ProblemDiscoveryGraph``。

    Returns:
        解析得到的项目记忆字典。
    """
    with _lock:
        return _read_json(_PROJECT_MEMORY_PATH, _PROJECT_MEMORY_DEFAULT)


def update_project_memory(**updates: Any) -> dict[str, Any]:
    """将 ``updates`` 合并到项目记忆中并持久化。

    列表类型的字段（``keywords``、``research_index``、``data_metrics``、
    ``historical_judgments``）会被追加并去重；标量字段直接覆盖。

    Args:
        **updates: 需要合并到项目记忆中的字段。

    Returns:
        更新后的项目记忆字典。
    """
    with _lock:
        memory = _read_json(_PROJECT_MEMORY_PATH, _PROJECT_MEMORY_DEFAULT)
        for key, value in updates.items():
            if isinstance(memory.get(key), list) and isinstance(value, list):
                existing = memory[key]
                memory[key] = existing + [item for item in value if item not in existing]
            else:
                memory[key] = value
        _write_json(_PROJECT_MEMORY_PATH, memory)
        return memory


# --------------------------------------------------------------------------
# Agent 记忆 —— 领域隔离，四个子图共享
# --------------------------------------------------------------------------


def _agent_memory_path(agent_name: str) -> Path:
    safe_name = agent_name.replace("/", "_")
    return _AGENT_MEMORY_DIR / f"{safe_name}.json"


def load_agent_memory(agent_name: str) -> dict[str, Any]:
    """加载单个 Agent 的领域隔离记忆命名空间。

    Args:
        agent_name: 该 Agent 的 Harness 名称（如 ``"research_agent"``）。

    Returns:
        该 Agent 的记忆字典（尚无记录时为空字典）。
    """
    with _lock:
        return _read_json(_agent_memory_path(agent_name), {})


def update_agent_memory(agent_name: str, **updates: Any) -> dict[str, Any]:
    """将 ``updates`` 合并到单个 Agent 的记忆命名空间中并持久化。

    Args:
        agent_name: 该 Agent 的 Harness 名称。
        **updates: 需要合并到该 Agent 记忆中的字段。

    Returns:
        更新后的 Agent 记忆字典。
    """
    with _lock:
        path = _agent_memory_path(agent_name)
        memory = _read_json(path, {})
        for key, value in updates.items():
            if isinstance(memory.get(key), list) and isinstance(value, list):
                existing = memory[key]
                memory[key] = existing + [item for item in value if item not in existing]
            else:
                memory[key] = value
        _write_json(path, memory)
        return memory
