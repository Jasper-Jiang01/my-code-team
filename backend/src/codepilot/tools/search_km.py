"""KM（知识管理）搜索工具。

优先顺序：
1. ``KM_SEARCH_ENDPOINT`` HTTP 适配层（``GET ?q=&top_k=``）
2. 本机 ``oa-skills citadel``（配置 ``KM_MIS`` 后走学城 ``searchContent``）
3. 本地夹具（保证单测与未接入时图能跑通）
"""

from __future__ import annotations

import html
import json
import logging
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote

import httpx
from langchain_core.tools import tool

from codepilot.core.config import settings

logger = logging.getLogger(__name__)

_KM_DOC_URL = "https://km.sankuai.com/collabpage/{content_id}"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SNIPPET_SKIP = (
    "此内容为简化版 Markdown",
    "不可直接用于 createDocument",
    "============================================================",
    "文档内容（简化版",
)


class KMSearchError(RuntimeError):
    """当 KM 搜索后端失败或无法访问时抛出。"""


def _fixture_results(query: str, top_k: int) -> list[dict[str, Any]]:
    """本地夹具：保证研究阶段总能拿到带来源的证据，而不是空列表。"""
    templates = (
        ("内部研究摘录", "与「{q}」相关的既有研究结论与口径说明。"),
        ("竞品与案例", "围绕「{q}」的外部对标案例与可复用模式。"),
        ("风险与约束", "落地「{q}」时需要标注的数据口径、合规与依赖风险。"),
    )
    results: list[dict[str, Any]] = []
    for index, (kind, snippet_tpl) in enumerate(templates[:top_k], start=1):
        results.append(
            {
                "title": f"{kind}: {query[:80]}",
                "url": f"km://fixture/{quote(query, safe='')[:80]}/{index}",
                "snippet": snippet_tpl.format(q=query),
                "source": "km_fixture",
            }
        )
    return results


def _strip_hl(text: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", text or "")).replace("\xa0", " ").strip()


def _clip(text: str, limit: int) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if limit <= 0 or len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 1, 1)].rstrip() + "…"


def _extract_json_value(text: str) -> Any:
    """从混有 SSO 提示的输出中抽出第一个完整 JSON 对象或数组。"""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        escape = False
        for index, char in enumerate(text[start:], start=start):
            if in_str:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_str = False
                continue
            if char == '"':
                in_str = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _run_citadel(args: list[str], timeout: float) -> str:
    binary = shutil.which("oa-skills") or "oa-skills"
    cmd = [binary, "citadel", *args]
    mis = (settings.km_mis or "").strip()
    if mis:
        cmd.extend(["--mis", mis])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise KMSearchError("oa-skills 不在 PATH 中，无法调用学城 citadel") from exc
    except subprocess.TimeoutExpired as exc:
        raise KMSearchError(f"citadel {' '.join(args[:2])} timed out after {timeout}s") from exc
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    if proc.returncode != 0:
        raise KMSearchError(
            f"citadel {' '.join(args[:2])} failed rc={proc.returncode}: {combined[:500]}"
        )
    return combined


def _search_citadel(query: str, top_k: int) -> list[dict[str, Any]]:
    timeout = max(8.0, float(settings.km_citadel_timeout))
    raw = _run_citadel(
        ["searchContent", "--keyword", query, "--limit", str(top_k), "--raw"],
        timeout=timeout,
    )
    payload = _extract_json_value(raw)
    if payload is None:
        stripped = raw.strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            logger.error("search_km: citadel JSON parse failed, head=%r", raw[:400])
            raise KMSearchError("citadel searchContent returned no JSON object") from None
    if not isinstance(payload, dict):
        raise KMSearchError("citadel searchContent JSON is not an object")
    items = payload.get("items") or payload.get("results") or []
    if not isinstance(items, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in items[:top_k]:
        if not isinstance(item, dict):
            continue
        content_id = str(item.get("contentId") or item.get("content_id") or "").strip()
        title = _strip_hl(str(item.get("title") or ""))
        snippet = _strip_hl(str(item.get("contentBodyHl") or item.get("titleHl") or ""))
        if not content_id and not title:
            continue
        space = str(item.get("spaceName") or "").strip()
        rows.append(
            {
                "content_id": content_id,
                "title": title or content_id,
                "url": _KM_DOC_URL.format(content_id=content_id) if content_id else "",
                "snippet": snippet,
                "source": f"xuecheng:{space}" if space else "xuecheng",
            }
        )
    return rows


def _parse_simple_markdown(raw: str) -> str:
    lines: list[str] = []
    seen_body = False
    for line in raw.splitlines():
        if any(marker in line for marker in _SNIPPET_SKIP):
            continue
        if not seen_body and line.strip() == "---":
            seen_body = True
            continue
        if not seen_body:
            continue
        lines.append(line)
    body = "\n".join(lines).strip()
    return body or _strip_hl(raw)


def _fetch_body(content_id: str) -> str:
    timeout = max(8.0, float(settings.km_citadel_timeout))
    raw = _run_citadel(
        ["getSimpleMarkdown", "--contentId", str(content_id)],
        timeout=timeout,
    )
    return _parse_simple_markdown(raw)


def _enrich_snippets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    max_chars = int(settings.km_snippet_max_chars or 1500)
    top_n = int(settings.km_fetch_body_top_k or 0)
    targets = [row for row in rows[: max(top_n, 0)] if row.get("content_id")]
    fetched: dict[str, str] = {}
    if targets:
        workers = min(3, len(targets))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_body, str(row["content_id"])): str(row["content_id"])
                for row in targets
            }
            for future in as_completed(futures):
                content_id = futures[future]
                try:
                    fetched[content_id] = future.result()
                except Exception:
                    logger.exception("search_km: failed to fetch body for %s", content_id)

    for row in rows:
        body = fetched.get(str(row.get("content_id") or ""))
        snippet = body or row.get("snippet") or row.get("title") or ""
        row["snippet"] = _clip(str(snippet), max_chars)
    return rows


def _http_search(query: str, top_k: int) -> list[dict[str, Any]]:
    endpoint = (settings.km_search_endpoint or "").strip()
    response = httpx.get(
        endpoint,
        params={"q": query, "top_k": top_k},
        timeout=8.0,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("results", [])
    return [row for row in rows if isinstance(row, dict)][:top_k]


@tool
def search_km(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """在内部知识库（学城 / KM）中搜索相关文档。

    Args:
        query: 搜索查询字符串。
        top_k: 需要返回的结果数量。

    Returns:
        搜索结果字典列表，每个字典预期至少包含 ``title``、
        ``url``、``snippet`` 和 ``source`` 键。
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    endpoint = (settings.km_search_endpoint or "").strip()
    if endpoint:
        try:
            parsed = _http_search(query, top_k)
            if parsed:
                return parsed
        except Exception:
            logger.exception(
                "search_km: backend %s failed, trying citadel/fixture", endpoint
            )

    if (settings.km_mis or "").strip():
        try:
            rows = _search_citadel(query, top_k)
            if rows:
                return _enrich_snippets(rows)[:top_k]
            logger.info("search_km: citadel returned no hits for query=%r", query)
            return []
        except Exception:
            logger.exception("search_km: citadel failed; not using fixture because KM_MIS is set")
            return []

    return _fixture_results(query, top_k)
