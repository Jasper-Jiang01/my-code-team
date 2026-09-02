"""简单/复杂分流：简单问答短路，复杂任务才进四段思考链。"""

import importlib

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END
from langgraph.types import Command

from codepilot.graphs.main_workflow import build_main_workflow
from codepilot.nodes.fast_qa import _fallback_answer, fast_qa
from codepilot.nodes.triage import match_complexity, triage

fast_qa_mod = importlib.import_module("codepilot.nodes.fast_qa")


def test_match_complexity_simple_questions():
    assert match_complexity("经营周报点击率口径是什么") == "fast_qa"
    assert match_complexity("按摩SPA货架方案怎么设计的？") == "fast_qa"
    assert match_complexity("研究商家周报点击率口径") == "fast_qa"


def test_match_complexity_complex_production_tasks():
    assert match_complexity("做一个商家供给冷启动 Demo") == "complex"
    assert match_complexity("帮我实现经营周报页面") == "complex"
    assert match_complexity("review 这段代码并改一下") == "complex"
    assert match_complexity("帮我出个页面原型图") == "complex"


def test_triage_routes_simple_to_fast_qa():
    cmd = triage({"goal": "点击率口径是什么"})  # type: ignore[arg-type]
    assert isinstance(cmd, Command)
    assert cmd.goto == "fast_qa"


def test_triage_routes_complex_to_classify():
    cmd = triage({"goal": "做一个商家供给冷启动 Demo"})  # type: ignore[arg-type]
    assert isinstance(cmd, Command)
    assert cmd.goto == "classify"


def test_fast_qa_returns_direct_reply_without_pipeline(monkeypatch):
    monkeypatch.setattr(
        fast_qa_mod,
        "_retrieve",
        lambda _: [
            {
                "title": "经营周报点击率口径",
                "url": "https://km.sankuai.com/collabpage/1",
                "snippet": "点击率 = 点击 UV / 触达 UV",
                "source": "xuecheng",
            }
        ],
    )
    monkeypatch.setattr(fast_qa_mod, "_answer_with_llm", lambda goal, evidence: None)
    cmd = fast_qa({"goal": "点击率口径是什么"})  # type: ignore[arg-type]
    assert cmd.goto == END
    assert cmd.update is not None
    reply = str(cmd.update.get("chitchat_reply", ""))
    assert "点击率" in reply
    assert "km.sankuai.com" in reply
    assert cmd.update.get("checkpoints") == ["fast_qa"]


def test_fallback_answer_without_hits():
    text = _fallback_answer("不存在的指标xyz", [])
    assert "没有检索到" in text


def test_main_workflow_simple_question_skips_production(monkeypatch):
    monkeypatch.setattr(
        fast_qa_mod,
        "_retrieve",
        lambda _: [{"title": "口径", "url": "https://km.sankuai.com/collabpage/1", "snippet": "定义"}],
    )
    monkeypatch.setattr(
        fast_qa_mod,
        "_answer_with_llm",
        lambda goal, evidence: "点击率口径见学城文档。",
    )
    graph = build_main_workflow(checkpointer=MemorySaver())
    result = graph.invoke(
        {"userMessage": "经营周报点击率口径是什么"},
        config={"configurable": {"thread_id": "fast-qa-1"}},
    )
    assert result.get("chitchat_reply") == "点击率口径见学城文档。"
    assert "fast_qa" in (result.get("checkpoints") or [])
    assert result.get("spec") is None
    assert not result.get("demo_artifact")
