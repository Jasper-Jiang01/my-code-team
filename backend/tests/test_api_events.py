"""最小 DeepAgent 图事件到 SSE 的映射测试。"""

from codepilot.api.events import interrupt_info, map_stream_item, map_updates, sse_frame


def test_map_epilogue_reply_as_token() -> None:
    events = map_updates({"epilogue": {"chitchat_reply": "你好"}})
    assert events == [{"type": "token", "content": "你好"}]


def test_map_ignores_non_epilogue_updates() -> None:
    assert map_updates({"legacy_node": {"chitchat_reply": "不应显示"}}) == []


def test_streamed_answer_is_not_duplicated_by_updates() -> None:
    payload = {"epilogue": {"chitchat_reply": "完整答案"}}
    assert map_stream_item("updates", payload, answer_streamed=True) == []


def test_unsupported_stream_modes_are_ignored() -> None:
    assert map_stream_item("messages", {"content": "内部消息"}) == []
    assert map_stream_item("custom", {"type": "token", "content": "内部消息"}) == []


def test_sse_frame_is_event_stream() -> None:
    frame = sse_frame("token", {"content": "hi"})
    assert frame.startswith("event: token\n")
    assert '"content": "hi"' in frame
    assert frame.endswith("\n\n")


def test_hitl_interrupt_maps_to_approval_event() -> None:
    """HITLRequest 中断应翻译成带工具详情的审批事件。"""
    payload = {
        "__interrupt__": (
            {
                "action_requests": [
                    {
                        "name": "write_file",
                        "args": {"file_path": "/notes/a.txt", "content": "hi"},
                        "description": "写入文件 /notes/a.txt",
                    }
                ],
                "review_configs": [
                    {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]}
                ],
            },
        )
    }
    events = map_updates(payload)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "interrupt"
    assert event["reason"] == "hitl"
    assert "write_file" in event["prompt"] or "写入文件" in event["prompt"]


def test_interrupt_info_supports_custom_prompt_payload() -> None:
    info = interrupt_info(({"prompt": "确认继续？", "reason": "custom"},))
    assert info == {"prompt": "确认继续？", "reason": "custom"}


def test_interrupt_info_falls_back_to_generic_prompt() -> None:
    assert interrupt_info(({},))["prompt"].startswith("工作流等待人工确认")
