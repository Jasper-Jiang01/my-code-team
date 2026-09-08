"""将最小主图的状态更新映射为聊天 SSE 事件。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

# chitchat_reply 的写入者：主图的 epilogue 节点。
_FINAL_REPLY_NODE = "epilogue"


def sse_frame(event: str, data: Mapping[str, Any]) -> str:
    """序列化一帧 Server-Sent Event。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def map_stream_item(
    mode: str,
    payload: Any,
    *,
    answer_streamed: bool = False,
) -> list[dict[str, Any]]:
    """从 ``epilogue`` 更新提取最终答复，从 ``__interrupt__`` 提取审批请求。"""
    if mode != "updates":
        return []
    return map_updates(payload, answer_streamed=answer_streamed)


def map_updates(data: Any, *, answer_streamed: bool = False) -> list[dict[str, Any]]:
    """映射 ``updates`` 流：``__interrupt__`` 与 ``epilogue.chitchat_reply``。"""
    payload = _unwrap_update(data)
    if not isinstance(payload, dict):
        return []
    events: list[dict[str, Any]] = []

    interrupt = payload.get("__interrupt__")
    if interrupt is not None:
        info = interrupt_info(interrupt)
        events.append(
            {"type": "interrupt", "prompt": info["prompt"], "reason": info.get("reason")}
        )

    value = payload.get(_FINAL_REPLY_NODE)
    if isinstance(value, dict) and not answer_streamed:
        reply = value.get("chitchat_reply")
        if isinstance(reply, str) and reply:
            events.append({"type": "token", "content": reply})
    return events


def interrupt_info(value: Any) -> dict[str, str | None]:
    """把 interrupt 载荷翻译成前端可展示的审批提示。

    支持两类载荷：
    - HumanInTheLoopMiddleware 的 ``HITLRequest``（``action_requests`` 列表，
      每项含 ``name`` / ``args`` / 可选 ``description``）；
    - 任意带 ``prompt`` 字段的自定义中断载荷。
    """
    first = value[0] if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value else value
    inner: Any = first
    if isinstance(first, dict) and "value" in first:
        inner = first["value"]
    elif not isinstance(first, dict) and hasattr(first, "value"):
        inner = first.value

    if isinstance(inner, dict):
        requests = inner.get("action_requests")
        if isinstance(requests, list) and requests:
            parts: list[str] = []
            for request in requests:
                if not isinstance(request, dict):
                    continue
                name = str(request.get("name") or "操作")
                description = str(request.get("description") or "").strip()
                args = request.get("args") if isinstance(request.get("args"), dict) else {}
                arg_text = "，".join(
                    f"{key}={val}" for key, val in list(args.items())[:3] if val is not None
                )
                label = description or name
                parts.append(f"{label}（{arg_text}）" if arg_text else label)
            if parts:
                return {
                    "prompt": "以下操作需要人工确认：" + "；".join(parts),
                    "reason": "hitl",
                }
        prompt = inner.get("prompt")
        if isinstance(prompt, str) and prompt:
            reason = inner.get("reason")
            return {"prompt": prompt, "reason": reason if isinstance(reason, str) else None}
    return {"prompt": "工作流等待人工确认，请审批后继续。", "reason": None}


def _unwrap_update(data: Any) -> Any:
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)) and data:
        return data[0]
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data
