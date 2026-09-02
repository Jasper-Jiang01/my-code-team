"""简单问答：检索学城/夹具证据后直接作答，不进研究→生产→评审链。"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command

from codepilot.core.create_model import create_chat_model
from codepilot.core.llm_utils import safe_content
from codepilot.states.workflow_state import WorkflowState, user_text
from codepilot.tools import search_km

logger = logging.getLogger(__name__)

_MAX_EVIDENCE = 3
_SNIPPET_CHARS = 800

_SYSTEM_PROMPT = """\
你是 CodePilot 的问答助手。根据检索到的证据直接回答用户问题。

规则：
- 有证据时基于证据作答，不要编造口径或来源。
- 文末用列表给出来源标题和 URL（若有）。
- 没有可用证据时明确说「知识库没有找到」，并给出下一步建议。
- 只输出对用户可见的回答，不要输出 JSON，不要提起研究/生产/评审流水线。
"""


def _as_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _retrieve(goal: str) -> list[dict[str, Any]]:
    try:
        rows = _as_records(search_km.invoke({"query": goal, "top_k": _MAX_EVIDENCE}))
    except Exception:
        logger.exception("fast_qa: search_km failed for goal=%r", goal[:80])
        return []
    return [
        item
        for item in rows
        if str(item.get("title") or item.get("snippet") or item.get("content") or "").strip()
    ][:_MAX_EVIDENCE]


def _format_evidence(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（无检索结果）"
    blocks: list[str] = []
    for index, item in enumerate(rows, start=1):
        title = str(item.get("title") or "未标题")
        url = str(item.get("url") or "")
        snippet = str(item.get("snippet") or item.get("content") or "")[:_SNIPPET_CHARS]
        source = str(item.get("source") or "")
        blocks.append(f"[{index}] {title}\nsource: {source}\nurl: {url}\n{snippet}")
    return "\n\n".join(blocks)


def _fallback_answer(goal: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"没有检索到与「{goal}」直接相关的学城文档。"
            "可以换个关键词再问，或者说「做一个 … Demo」走完整生产流程。"
        )
    lines = [f"根据检索，与「{goal}」相关的资料如下：", ""]
    for item in rows:
        title = str(item.get("title") or "未标题")
        url = str(item.get("url") or "")
        snippet = str(item.get("snippet") or "").strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:159] + "…"
        lines.append(f"- {title}" + (f"\n  {url}" if url else ""))
        if snippet:
            lines.append(f"  {snippet}")
    return "\n".join(lines)


def _answer_with_llm(goal: str, evidence: str) -> str | None:
    try:
        model = create_chat_model(timeout=45, max_retries=1)
        response = model.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=f"用户问题：{goal}\n\n检索证据：\n{evidence}"
                ),
            ]
        )
        text = safe_content(response).strip()
        return text or None
    except Exception:
        logger.exception("fast_qa: LLM answer failed")
        return None


def fast_qa(state: WorkflowState) -> Command[Literal["__end__"]]:
    """检索 + 直接作答，然后结束。"""
    goal = user_text(state)
    rows = _retrieve(goal) if goal else []
    evidence = _format_evidence(rows)
    answer = _answer_with_llm(goal, evidence) if goal else None
    if not answer:
        answer = _fallback_answer(goal or "（空问题）", rows)
    logger.info("fast_qa: hits=%s goal=%r", len(rows), goal[:80])
    return Command(
        update={
            "chitchat_reply": answer,
            "checkpoints": ["fast_qa"],
        },
        goto=END,
    )
