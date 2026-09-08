"""主工作流（含意图路由 + HumanInTheLoop）的回归测试。"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from codepilot.graphs.main_workflow import (
    IntentRouter,
    build_deep_agent,
    build_main_workflow,
)


class _ToolBindableFakeChatModel(FakeMessagesListChatModel):
    """为 DeepAgent 提供工具绑定接口的离线聊天模型。"""

    def bind_tools(self, tools, **kwargs):  # type: ignore[no-untyped-def]
        del tools, kwargs
        return self


def _agent_with_reply(reply: str):
    return build_deep_agent(
        model=_ToolBindableFakeChatModel(responses=[AIMessage(content=reply)])
    )


def _router_model(reply: str = "general"):
    """离线意图分类模型：固定返回给定标签。"""
    return FakeMessagesListChatModel(responses=[AIMessage(content=reply)])


def _fake_data_graph(reply: str = "来自数据分析子图的结论"):
    """固定回复的假数据分析子图（与主图共享 messages 通道）。"""

    class _DataState(TypedDict):
        messages: Annotated[list[AnyMessage], add_messages]

    def reply_node(state: _DataState) -> dict[str, list[AnyMessage]]:
        return {"messages": [AIMessage(content=reply)]}

    builder = StateGraph(_DataState)
    builder.add_node("reply", reply_node)
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    return builder.compile()


def test_main_workflow_topology_includes_intent_route_and_both_executors():
    graph = build_main_workflow(
        agent=_agent_with_reply("完成"),
        data_graph=_fake_data_graph(),
        router_model=_router_model(),
    )

    assert set(graph.nodes) == {
        "__start__",
        "prelude",
        "intent_route",
        "deep_agent",
        "data_analysis",
        "epilogue",
    }


def test_general_intent_routes_to_deep_agent():
    graph = build_main_workflow(
        checkpointer=MemorySaver(),
        agent=_agent_with_reply("由 DeepAgent 生成的答复"),
        data_graph=_fake_data_graph("不应被调用"),
        router_model=_router_model("general"),
    )

    result = graph.invoke(
        {"userMessage": "请处理这个请求"},
        config={"configurable": {"thread_id": "general-route"}},
    )

    assert result["chitchat_reply"] == "由 DeepAgent 生成的答复"
    assert result["checkpoints"] == ["deep_agent"]


def test_data_analysis_intent_routes_to_data_graph():
    graph = build_main_workflow(
        checkpointer=MemorySaver(),
        agent=_agent_with_reply("不应被调用"),
        data_graph=_fake_data_graph("数据分析结论"),
        router_model=_router_model("data_analysis"),
    )

    result = graph.invoke(
        {"userMessage": "帮我分析销售额趋势"},
        config={"configurable": {"thread_id": "data-route"}},
    )

    assert result["chitchat_reply"] == "数据分析结论"
    assert result["checkpoints"] == ["data_analysis"]


def test_data_file_mention_fast_paths_to_data_graph_without_llm():
    """消息明确提到 .csv 时走关键词快速通道，路由模型不应被调用。"""
    graph = build_main_workflow(
        checkpointer=MemorySaver(),
        agent=_agent_with_reply("不应被调用"),
        data_graph=_fake_data_graph("EDA 完成"),
        # 路由模型无任何响应：若被调用会抛错，证明快速通道未走 LLM
        router_model=FakeMessagesListChatModel(responses=[]),
    )

    result = graph.invoke(
        {"userMessage": "分析 sales.csv，哪个城市销售额最高"},
        config={"configurable": {"thread_id": "csv-fast-path"}},
    )

    assert result["chitchat_reply"] == "EDA 完成"
    assert result["checkpoints"] == ["data_analysis"]


def test_intent_router_falls_back_to_general_on_bad_output():
    router = IntentRouter(model=_router_model("我觉得这像是数据分析任务"))

    assert router.classify("帮我对各城市做经营诊断") == "general"


def test_intent_router_falls_back_to_general_on_error():
    class _BrokenModel:
        def invoke(self, messages):  # type: ignore[no-untyped-def]
            raise RuntimeError("network down")

    router = IntentRouter(model=_BrokenModel())  # type: ignore[arg-type]

    assert router.classify("分析一下各渠道转化率") == "general"


def test_main_workflow_blank_input_short_circuits_before_agent():
    graph = build_main_workflow(
        agent=_agent_with_reply("不应被调用"),
        data_graph=_fake_data_graph("不应被调用"),
        router_model=_router_model(),
    )

    result = graph.invoke(
        {"userMessage": "   "},
        config={"configurable": {"thread_id": "blank-input"}},
    )

    assert result["chitchat_reply"] == "请输入具体问题或任务。"
    assert result["checkpoints"] == ["prelude"]


def test_main_workflow_accepts_user_message_and_optional_intent():
    graph = build_main_workflow(
        agent=_agent_with_reply("完成"),
        data_graph=_fake_data_graph(),
        router_model=_router_model(),
    )
    schema = graph.get_input_jsonschema()

    # intent 为可选字段：不传时走意图自动分类，传 data_analysis 时直接进子图
    assert set(schema["properties"]) == {"userMessage", "intent"}
    assert schema["required"] == ["userMessage"]


def test_forced_intent_bypasses_classification():
    """显式携带 intent=data_analysis 时跳过分类，直接进入数据分析子图。"""
    graph = build_main_workflow(
        checkpointer=MemorySaver(),
        agent=_agent_with_reply("不应被调用"),
        data_graph=_fake_data_graph("直接进入分析子图"),
        # 路由模型固定输出 general：若强制意图失效会被分到 deep_agent 而测试失败
        router_model=_router_model("general"),
    )

    result = graph.invoke(
        {"userMessage": "帮我写个脚本", "intent": "data_analysis"},
        config={"configurable": {"thread_id": "forced-intent"}},
    )

    assert result["chitchat_reply"] == "直接进入分析子图"
    assert result["checkpoints"] == ["data_analysis"]


def test_invalid_forced_intent_falls_back_to_classification():
    """非法 intent 值被忽略，回退自动分类。"""
    graph = build_main_workflow(
        checkpointer=MemorySaver(),
        agent=_agent_with_reply("通用回复"),
        data_graph=_fake_data_graph("不应被调用"),
        router_model=_router_model("general"),
    )

    result = graph.invoke(
        {"userMessage": "帮我写个脚本", "intent": "hacker_intent"},
        config={"configurable": {"thread_id": "invalid-intent"}},
    )

    assert result["chitchat_reply"] == "通用回复"
    assert result["checkpoints"] == ["deep_agent"]


def _pending_hitl_request(graph, config) -> dict:
    """读取挂起的 HITL 中断载荷。"""
    state = graph.get_state(config)
    for task in state.tasks:
        for interrupt in task.interrupts:
            value = getattr(interrupt, "value", None)
            if isinstance(value, dict) and "action_requests" in value:
                return value
    raise AssertionError("expected a pending HumanInTheLoop interrupt")


def test_write_tool_call_interrupts_for_human_approval():
    """DeepAgent 发起写文件调用时应冒泡 HITL 中断，approve 后继续执行。"""
    model = _ToolBindableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/notes/hello.txt", "content": "hi"},
                        "id": "call_write_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已写入 notes/hello.txt"),
        ]
    )
    graph = build_main_workflow(
        checkpointer=MemorySaver(),
        agent=build_deep_agent(model=model),
        data_graph=_fake_data_graph(),
        router_model=_router_model(),
    )
    config = {"configurable": {"thread_id": "hitl-approve"}}

    graph.invoke({"userMessage": "帮我写 /notes/hello.txt"}, config=config)

    request = _pending_hitl_request(graph, config)
    actions = request["action_requests"]
    assert len(actions) == 1
    assert actions[0]["name"] == "write_file"
    assert actions[0]["args"]["file_path"] == "/notes/hello.txt"

    result = graph.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)
    assert result["chitchat_reply"] == "已写入 notes/hello.txt"
    assert result["checkpoints"] == ["deep_agent"]


def test_write_tool_call_reject_reports_back_to_model():
    """驳回写操作后，模型应收到拒绝信息并继续生成答复。"""
    model = _ToolBindableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "edit_file",
                        "args": {"file_path": "/notes/a.txt", "old_string": "a", "new_string": "b"},
                        "id": "call_edit_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="好的，我不改这个文件了。"),
        ]
    )
    graph = build_main_workflow(
        checkpointer=MemorySaver(),
        agent=build_deep_agent(model=model),
        data_graph=_fake_data_graph(),
        router_model=_router_model(),
    )
    config = {"configurable": {"thread_id": "hitl-reject"}}

    graph.invoke({"userMessage": "把 /notes/a.txt 里的 a 改成 b"}, config=config)
    request = _pending_hitl_request(graph, config)
    assert request["action_requests"][0]["name"] == "edit_file"

    result = graph.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "不要动这个文件"}]}),
        config=config,
    )
    assert result["chitchat_reply"] == "好的，我不改这个文件了。"
