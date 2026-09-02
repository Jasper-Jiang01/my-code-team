"""用于项目与 Agent 记忆存储的向量记忆工具。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool

from codepilot.core.config import settings

_VALID_ACTIONS = ("add", "search")
_lock = threading.Lock()
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_STORE = _BACKEND_ROOT / "memory" / "vector_store.json"
_EMBED_DIM = 128

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class VectorMemoryError(RuntimeError):
    """当向量记忆后端失败时抛出。"""


def _store_path() -> Path:
    override = (settings.vector_store_url or "").strip()
    if override.startswith("/") or override.startswith("file:"):
        return Path(override.removeprefix("file://"))
    return _DEFAULT_STORE


def _load_store() -> dict[str, list[dict[str, Any]]]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        raise VectorMemoryError(f"Failed to read vector store {path}: {exc}") from exc
    if not isinstance(raw, dict):
        return {}
    return {str(key): list(value) for key, value in raw.items() if isinstance(value, list)}


def _save_store(store: dict[str, list[dict[str, Any]]]) -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise VectorMemoryError(f"Failed to write vector store {path}: {exc}") from exc


def _blob(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "snippet", "value", "metric", "definition", "title", "query", "content"):
        value = record.get(key)
        if value:
            parts.append(str(value))
    if not parts:
        parts.append(json.dumps({k: v for k, v in record.items() if k != "embedding"}, ensure_ascii=False))
    return " ".join(parts)


def _tokens(text: str) -> list[str]:
    words = [token.lower() for token in _TOKEN_RE.findall(text) if token.strip()]
    grams: list[str] = []
    for word in words:
        if any("\u4e00" <= char <= "\u9fff" for char in word):
            grams.extend(list(word))
            grams.extend(word[i : i + 2] for i in range(len(word) - 1))
        grams.append(word)
    return grams


def embed_text(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """把文本哈希成固定维度向量（signed bag-of-words），用于离线 cosine 检索。"""
    vec = [0.0] * dim
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % dim
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vec[index] += sign
    norm = math.sqrt(sum(value * value for value in vec))
    if norm == 0:
        return vec
    return [value / norm for value in vec]


def _as_vector(record: dict[str, Any]) -> list[float]:
    stored = record.get("embedding")
    if isinstance(stored, list) and len(stored) == _EMBED_DIM:
        return [float(value) for value in stored]
    return embed_text(_blob(record))


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


@tool
def vector_memory(
    action: Literal["add", "search"],
    collection: str,
    data: dict[str, Any] | None = None,
    query: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]] | bool:
    """与向量记忆存储进行交互。

    记录会写入 embedding 向量，检索按 cosine 相似度排序。
    接口与后续替换为 Chroma / Pinecone 保持兼容。

    Args:
        action: ``"add"`` 表示插入一条记录，``"search"`` 表示查询。
        collection: 记忆集合名称（如 ``project_memory``、
            ``agent_memory.research``）。
        data: 需要添加的数据（当 ``action="add"`` 时必需）。
        query: 查询字符串（当 ``action="search"`` 时必需）。
        top_k: 搜索返回的结果数量。
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

    with _lock:
        store = _load_store()
        bucket = store.setdefault(collection, [])
        if action == "add":
            record = dict(data or {})
            record["embedding"] = embed_text(_blob(record))
            existing_ids = {
                item.get("id") for item in bucket if isinstance(item, dict) and item.get("id")
            }
            if record.get("id") and record["id"] in existing_ids:
                bucket[:] = [
                    record if isinstance(item, dict) and item.get("id") == record["id"] else item
                    for item in bucket
                ]
            else:
                bucket.append(record)
            _save_store(store)
            return True

        query_vec = embed_text(query or "")
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in bucket:
            if not isinstance(item, dict):
                continue
            score = _cosine(query_vec, _as_vector(item))
            if score > 0:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        hits = []
        for score, item in ranked[:top_k]:
            payload = {k: v for k, v in item.items() if k != "embedding"}
            payload["score"] = round(score, 4)
            hits.append(payload)
        return hits
