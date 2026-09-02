"""chitchat 入口闸门：模板短路 + Command 路由。"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END
from langgraph.types import Command

from codepilot.graphs.main_workflow import build_main_workflow
from codepilot.nodes.chitchat import chitchat, match_chitchat
from codepilot.states.workflow_state import WorkflowInput


def test_match_chitchat_greetings_and_identity():
    assert match_chitchat("你好") == "greeting"
    assert match_chitchat("Hello!") == "greeting"
    assert match_chitchat("谢谢") == "thanks"
    assert match_chitchat("你是谁") == "identity"
    assert match_chitchat("你会什么？") == "identity"
    assert match_chitchat("你会做什么") == "identity"
    assert match_chitchat("") == "empty"


def test_match_chitchat_rejects_real_tasks():
    # 旧启发式会把「我要做周报」误判为闲聊
    assert match_chitchat("我要做周报") is None
    assert match_chitchat("帮我查经营周报点击率口径") is None
    assert match_chitchat("做一个商家供给冷启动 Demo") is None
    assert match_chitchat("你帮我分析一下这份口径文档里的指标定义") is None


def test_chitchat_command_ends_with_template_reply():
    cmd = chitchat({"goal": "你好"})  # type: ignore[arg-type]
    assert isinstance(cmd, Command)
    assert cmd.goto == END
    assert cmd.update is not None
    assert "CodePilot" in str(cmd.update.get("chitchat_reply", ""))
    assert "next_step" not in cmd.update


def test_chitchat_command_passes_to_triage():
    cmd = chitchat({"goal": "研究商家周报点击率口径"})  # type: ignore[arg-type]
    assert isinstance(cmd, Command)
    assert cmd.goto == "triage"
    assert cmd.update is not None
    assert cmd.update.get("goal") == "研究商家周报点击率口径"
    assert cmd.update.get("userMessage") == "研究商家周报点击率口径"


def test_main_workflow_chitchat_short_circuits_without_classify():
    graph = build_main_workflow(checkpointer=MemorySaver())
    result = graph.invoke(
        {"userMessage": "你好"},
        config={"configurable": {"thread_id": "chitchat-hi"}},
    )
    assert result.get("chitchat_reply")
    assert "CodePilot" in result["chitchat_reply"]
    assert result.get("spec") is None
    assert not result.get("demo_artifact")
    assert not result.get("facts_ledger")
    assert any(
        isinstance(c, str) and c.startswith("chitchat:")
        for c in (result.get("checkpoints") or [])
    )


def test_main_workflow_input_schema_is_user_message():
    graph = build_main_workflow()
    schema = graph.get_input_jsonschema()
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    assert "userMessage" in properties
    assert required == ["userMessage"]
    assert "facts_ledger" not in properties
    assert "constraints" not in properties


def test_workflow_input_is_plain_text():
    assert WorkflowInput(userMessage="做个 Demo").userMessage == "做个 Demo"
    assert WorkflowInput.model_validate({"goal": "查口径"}).userMessage == "查口径"
