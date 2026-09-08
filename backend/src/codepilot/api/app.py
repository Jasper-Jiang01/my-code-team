"""内网 FastAPI：编译最小主图并对外提供聊天 SSE。"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from codepilot.api.events import map_stream_item, sse_frame
from codepilot.core.checkpointer import create_async_checkpointer
from codepilot.graphs.main_workflow import build_main_workflow

logger = logging.getLogger(__name__)
_STARTED_AT_EPOCH = time.time()
_STARTED_AT_ISO = datetime.fromtimestamp(_STARTED_AT_EPOCH, tz=UTC).isoformat(timespec="seconds")
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND_BUILD = Path(__file__).resolve().with_name("static")
load_dotenv(_BACKEND_ROOT / ".env")

_DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://ws.cloud.test.sankuai.com",
    "https://codepilot.ai.test.sankuai.com",
    "https://jasper-jiang01.github.io",
]


def _allowed_origins() -> list[str]:
    extra = [
        origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()
    ]
    return _DEFAULT_ORIGINS + extra


class ChatRequest(BaseModel):
    """最小聊天请求，仅包含文本与会话标识。"""

    message: str = Field(min_length=1, description="用户输入")
    session_id: str | None = None
    # 可选：显式指定意图（如前端“数据分析专家”快捷入口传 data_analysis），
    # 跳过意图分类直接进入对应子图；留空则自动判定。非法值会被后端忽略。
    intent: str | None = Field(
        default=None, description="可选：显式意图（data_analysis / general）"
    )


class ResumeRequest(BaseModel):
    """人工审批请求：approve / reject 由后端翻译为 HITL 决策。"""

    session_id: str = Field(min_length=1)
    approved: bool
    comment: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if getattr(app.state, "graph", None) is None:
        app.state.graph = build_main_workflow(checkpointer=await create_async_checkpointer())
        logger.info("compiled minimal DeepAgent workflow")
    yield


app = FastAPI(title="CodePilot Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount(
    "/assets",
    StaticFiles(directory=_FRONTEND_BUILD / "assets", check_dir=False),
    name="frontend-assets",
)


async def get_graph(request: Request) -> Any:
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        graph = build_main_workflow(checkpointer=await create_async_checkpointer())
        request.app.state.graph = graph
    return graph


async def _stream_graph(graph: Any, payload: dict[str, str], thread_id: str) -> AsyncIterator[str]:
    yield sse_frame("session", {"session_id": thread_id})
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    try:
        async for item in graph.astream(payload, config=config, stream_mode="updates"):
            for event in map_stream_item("updates", item):
                yield sse_frame(event["type"], event)
        yield sse_frame("done", {"session_id": thread_id})
    except Exception:
        logger.exception("workflow run failed thread=%s", thread_id)
        yield sse_frame(
            "error",
            {"code": "AGENT_ERROR", "message": "生成回答时出现问题，请稍后重试。"},
        )
        yield sse_frame("done", {"session_id": thread_id})


def _sse(chunks: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        chunks,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
@app.get("/ok")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "started_at": _STARTED_AT_ISO,
        "started_at_epoch": f"{_STARTED_AT_EPOCH:.0f}",
    }


@app.get("/logo.png", include_in_schema=False)
def logo() -> FileResponse:
    """返回 Vite public 目录中的站点图标。"""
    icon = _FRONTEND_BUILD / "logo.png"
    if not icon.is_file():
        raise HTTPException(status_code=404, detail="frontend logo is unavailable")
    return FileResponse(icon, media_type="image/png")


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    thread_id = (req.session_id or "").strip() or str(uuid.uuid4())
    graph = await get_graph(request)
    # 显式 intent（快捷入口）随输入传入图；留空则由 intent_route 自动判定
    graph_input = {"userMessage": req.message}
    if (req.intent or "").strip():
        graph_input["intent"] = req.intent.strip()
    return _sse(_stream_graph(graph, graph_input, thread_id))


async def _hitl_decisions(
    graph: Any,
    thread_id: str,
    approved: bool,
    comment: str | None,
) -> list[dict[str, Any]] | None:
    """读取挂起的人工审批请求，按数量构造 HITL 决策。

    HumanInTheLoopMiddleware 要求 decisions 与待审工具调用数严格相等，
    数量从 checkpointer 保存的 interrupt 状态读取。
    """
    try:
        state = await graph.aget_state(config={"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.exception("resume: aget_state failed thread=%s", thread_id)
        return None
    count = 0
    for task in getattr(state, "tasks", None) or []:
        for interrupt in getattr(task, "interrupts", None) or []:
            value = getattr(interrupt, "value", None)
            if isinstance(value, dict) and isinstance(value.get("action_requests"), list):
                count += len(value["action_requests"])
    if count == 0:
        return None
    if approved:
        return [{"type": "approve"} for _ in range(count)]
    reason = (comment or "").strip() or "用户驳回了该操作"
    return [{"type": "reject", "message": reason} for _ in range(count)]


@app.post("/api/resume")
async def resume(req: ResumeRequest, request: Request) -> StreamingResponse:
    """审批挂起的 HumanInTheLoop 中断并继续流式输出。"""
    thread_id = req.session_id.strip()
    if not thread_id:
        raise HTTPException(status_code=400, detail="session_id required")
    graph = await get_graph(request)
    decisions = await _hitl_decisions(graph, thread_id, req.approved, req.comment)
    if decisions is not None:
        payload: Any = Command(resume={"decisions": decisions})
    else:
        # 非 HITL 中断或状态不可读：保持原契约透传，交由图自行处理。
        payload = {"approved": req.approved, "comment": req.comment}
    return _sse(_stream_graph(graph, payload, thread_id))


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str) -> FileResponse:
    """提供 Vite 构建的单页应用，并保留 /api 下的接口路由。"""
    index = _FRONTEND_BUILD / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="frontend build is unavailable")
    return FileResponse(index)
