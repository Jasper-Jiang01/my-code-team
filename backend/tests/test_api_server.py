"""FastAPI 最小聊天接口测试（含 HumanInTheLoop 审批）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from codepilot.api.app import app


class _FakeInterrupt:
    def __init__(self, value: Any) -> None:
        self.value = value


class _FakeTask:
    def __init__(self, interrupts: list[Any]) -> None:
        self.interrupts = interrupts


class _FakeState:
    def __init__(self, tasks: list[Any] | None = None) -> None:
        self.tasks = tasks or []


class _FakeGraph:
    def __init__(self, items: list[Any] | None = None) -> None:
        self.items = items or []
        self.last_input: Any = None
        self.last_config: Any = None
        # aget_state 返回值：默认无挂起中断
        self.state = _FakeState()

    async def astream(self, payload: Any, config: Any, stream_mode: Any) -> AsyncIterator[Any]:
        self.last_input = payload
        self.last_config = config
        assert stream_mode == "updates"
        for item in self.items:
            yield item

    async def aget_state(self, config: Any) -> _FakeState:
        self.last_config = config
        return self.state


@pytest.fixture
def api_client():
    fake = _FakeGraph()
    app.state.graph = fake
    with TestClient(app) as client:
        yield client, fake
    app.state.graph = None


def test_health(api_client: tuple[TestClient, _FakeGraph]) -> None:
    client, _fake = api_client
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ok_probe(api_client: tuple[TestClient, _FakeGraph]) -> None:
    client, _fake = api_client
    response = client.get("/ok")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_streams_epilogue_reply(api_client: tuple[TestClient, _FakeGraph]) -> None:
    client, fake = api_client
    fake.items = [{"epilogue": {"chitchat_reply": "收到"}}]

    response = client.post("/api/chat", json={"message": "你好", "session_id": "thread-1"})

    assert response.status_code == 200
    assert "event: session" in response.text
    assert "thread-1" in response.text
    assert "收到" in response.text
    assert "event: done" in response.text
    assert fake.last_input == {"userMessage": "你好"}
    assert fake.last_config["configurable"]["thread_id"] == "thread-1"


def test_chat_hides_non_epilogue_updates(api_client: tuple[TestClient, _FakeGraph]) -> None:
    client, fake = api_client
    fake.items = [
        {"legacy_node": {"chitchat_reply": "内部过程"}},
        {"epilogue": {"chitchat_reply": "最终答复"}},
    ]

    response = client.post("/api/chat", json={"message": "做个原型", "session_id": "thread-4"})

    assert response.status_code == 200
    assert "内部过程" not in response.text
    assert "最终答复" in response.text


def test_chat_streams_hitl_interrupt_event(api_client: tuple[TestClient, _FakeGraph]) -> None:
    """HITL 中断经 updates 流映射为 interrupt SSE 事件。"""
    client, fake = api_client
    fake.items = [
        {
            "__interrupt__": (
                {
                    "action_requests": [
                        {"name": "write_file", "args": {"file_path": "/notes/a.txt"}}
                    ],
                },
            )
        }
    ]

    response = client.post("/api/chat", json={"message": "写文件", "session_id": "thread-h"})

    assert response.status_code == 200
    assert "event: interrupt" in response.text
    assert "write_file" in response.text


def test_resume_approve_translates_to_hitl_decisions(
    api_client: tuple[TestClient, _FakeGraph],
) -> None:
    """approved=true 且挂起两个待审调用时，翻译为两个 approve 决策。"""
    client, fake = api_client
    fake.state = _FakeState(
        tasks=[
            _FakeTask(
                interrupts=[
                    _FakeInterrupt(
                        {
                            "action_requests": [
                                {"name": "write_file", "args": {}},
                                {"name": "edit_file", "args": {}},
                            ]
                        }
                    )
                ]
            )
        ]
    )
    fake.items = [{"epilogue": {"chitchat_reply": "已继续"}}]

    response = client.post(
        "/api/resume",
        json={"session_id": "thread-r", "approved": True},
    )

    assert response.status_code == 200
    assert "已继续" in response.text
    resume_input = fake.last_input
    assert hasattr(resume_input, "resume"), "应以 Command(resume=...) 恢复"
    decisions = resume_input.resume["decisions"]  # type: ignore[attr-defined]
    assert decisions == [{"type": "approve"}, {"type": "approve"}]


def test_resume_reject_includes_comment(api_client: tuple[TestClient, _FakeGraph]) -> None:
    client, fake = api_client
    fake.state = _FakeState(
        tasks=[
            _FakeTask(
                interrupts=[
                    _FakeInterrupt({"action_requests": [{"name": "write_file", "args": {}}]})
                ]
            )
        ]
    )
    fake.items = [{"epilogue": {"chitchat_reply": "已中止"}}]

    response = client.post(
        "/api/resume",
        json={"session_id": "thread-r2", "approved": False, "comment": "不要写"},
    )

    assert response.status_code == 200
    decisions = fake.last_input.resume["decisions"]  # type: ignore[attr-defined]
    assert decisions == [{"type": "reject", "message": "不要写"}]


def test_resume_without_hitl_falls_back_to_passthrough(
    api_client: tuple[TestClient, _FakeGraph],
) -> None:
    """无挂起 HITL 请求时保持旧契约透传 approved/comment。"""
    client, fake = api_client
    fake.items = [{"epilogue": {"chitchat_reply": "完成"}}]

    response = client.post(
        "/api/resume",
        json={"session_id": "thread-r3", "approved": True, "comment": "继续"},
    )

    assert response.status_code == 200
    assert fake.last_input == {"approved": True, "comment": "继续"}


def test_resume_requires_session_id(api_client: tuple[TestClient, _FakeGraph]) -> None:
    client, _fake = api_client
    response = client.post("/api/resume", json={"session_id": "", "approved": True})
    assert response.status_code == 422  # pydantic min_length 校验
