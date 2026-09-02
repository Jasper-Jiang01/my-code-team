"""需求意图 → 入口/工具白名单。"""

from codepilot.core.intent_router import match_intent, needs_tool, resolve_intent
from codepilot.nodes.classify_task import classify_task
from codepilot.nodes.production_steps import generate
from codepilot.nodes.route_task import (
    route_after_decision,
    route_after_production,
    route_after_research,
)
from codepilot.nodes.triage import match_complexity, triage
from langgraph.types import Command


def test_match_intent_knowledge_uses_search_km():
    intent = match_intent("经营周报点击率口径是什么")
    assert intent.kind == "knowledge"
    assert intent.entry == "fast_qa"
    assert intent.tools == ("search_km",)


def test_match_intent_prototype_only_pde():
    intent = match_intent("帮我出个页面原型图")
    assert intent.kind == "prototype"
    assert intent.entry == "produce"
    assert intent.tools == ("pde_prototype",)
    assert "query_sql" not in intent.tools
    assert "search_km" not in intent.tools


def test_match_intent_html_prototype_file():
    intent = match_intent("帮我出一份大众点评首页的html原型文件")
    assert intent.kind == "prototype"
    assert intent.entry == "produce"
    assert intent.tools == ("pde_prototype",)


def test_match_intent_spec_uses_pde_requirements():
    intent = match_intent("写一份需求文档")
    assert intent.kind == "spec"
    assert intent.entry == "produce"
    assert intent.pde_stage == "requirements"
    assert intent.tools == ("pde_prototype",)


def test_match_intent_full_demo_keeps_pipeline():
    intent = match_intent("做一个商家供给冷启动 Demo")
    assert intent.kind == "full"
    assert intent.entry == "research"
    assert "query_sql" in intent.tools
    assert "pde_prototype" in intent.tools


def test_match_intent_how_designed_is_still_qa():
    intent = match_intent("按摩SPA货架方案怎么设计的？")
    assert intent.kind == "knowledge"
    assert intent.entry == "fast_qa"


def test_match_complexity_still_compatible():
    assert match_complexity("经营周报点击率口径是什么") == "fast_qa"
    assert match_complexity("帮我出个页面原型图") == "complex"


def test_triage_routes_prototype_to_produce_without_sql():
    cmd = triage({"goal": "帮我出个页面原型图"})  # type: ignore[arg-type]
    assert isinstance(cmd, Command)
    assert cmd.goto == "produce"
    assert cmd.update is not None
    assert cmd.update["task_intent"] == "prototype"
    assert cmd.update["needed_tools"] == ["pde_prototype"]
    assert "query_sql" not in cmd.update["needed_tools"]


def test_triage_routes_full_demo_to_classify():
    cmd = triage({"goal": "做一个商家供给冷启动 Demo"})  # type: ignore[arg-type]
    assert cmd.goto == "classify"
    assert cmd.update is not None
    assert cmd.update["task_intent"] == "full"


def test_classify_skips_llm_for_prototype():
    out = classify_task({"goal": "帮我出个页面原型图"})  # type: ignore[arg-type]
    assert out["next_step"] == "produce"
    assert out["needed_tools"] == ["pde_prototype"]


def test_pipeline_stops_after_matching_subgraph():
    proto = {"goal": "帮我出个页面原型图", "task_intent": "prototype"}
    assert route_after_research(proto) == "end"  # type: ignore[arg-type]
    assert route_after_decision(proto) == "end"  # type: ignore[arg-type]
    assert route_after_production(proto) == "end"  # type: ignore[arg-type]
    full = {"goal": "做一个商家供给冷启动 Demo", "task_intent": "full"}
    assert route_after_research(full) == "data"  # type: ignore[arg-type]
    assert route_after_decision(full) == "produce"  # type: ignore[arg-type]
    assert route_after_production(full) == "qa"  # type: ignore[arg-type]


def test_needs_tool_respects_whitelist():
    state = {"goal": "帮我出个页面原型图", "task_intent": "prototype", "needed_tools": ["pde_prototype"]}
    assert needs_tool(state, "pde_prototype") is True
    assert needs_tool(state, "query_sql") is False


def test_resolve_intent_prefers_state_over_goal():
    intent = resolve_intent({"goal": "帮我出个页面原型图", "task_intent": "data", "needed_tools": ["query_sql"]})
    assert intent.kind == "data"
    assert intent.tools == ("query_sql",)


def test_generate_skips_pde_when_not_needed(monkeypatch):
    class _Spy:
        def __init__(self) -> None:
            self.called = False

        def invoke(self, *_args: object, **_kwargs: object) -> dict:
            self.called = True
            return {}

    spy = _Spy()
    monkeypatch.setattr("codepilot.nodes.production_steps.pde_prototype", spy)
    monkeypatch.setattr("codepilot.nodes.production_steps.invoke_agent", lambda *_a, **_k: "{}")
    result = generate(
        {  # type: ignore[arg-type]
            "goal": "取数看 GMV 规模测算",
            "task_intent": "data",
            "needed_tools": ["query_sql"],
        }
    )
    assert spy.called is False
    assert "prototype" not in (result.get("design_draft") or {})
