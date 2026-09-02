"""interrupt / resume 与 reopen 消费路径的集成测试（不依赖模型供应商）。"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from codepilot.graphs.review import build_review_graph
from codepilot.nodes.human_confirm import human_confirm
from codepilot.nodes.loop_control import after_qa
from codepilot.nodes.review_steps import rehearsal_gate
from codepilot.states.workflow_state import WorkflowState


def _tiny_confirm_graph():
    builder = StateGraph(WorkflowState)
    builder.add_node("human_confirm", human_confirm)
    builder.add_edge(START, "human_confirm")
    builder.add_edge("human_confirm", END)
    return builder.compile(checkpointer=MemorySaver())


def test_human_confirm_interrupt_then_resume():
    graph = _tiny_confirm_graph()
    config = {"configurable": {"thread_id": "human-confirm-1"}}
    state = {
        "goal": "g",
        "scope": "s",
        "issues_ledger": [
            {
                "id": "iss-1",
                "source": "qa",
                "risk": "high",
                "fix": "fix it",
                "evidence": "e",
                "status": "open",
            }
        ],
        "qa_report": None,
        "human_confirm": None,
    }
    first = graph.invoke(state, config=config)  # type: ignore[arg-type]
    assert "__interrupt__" in first

    resumed = graph.invoke(Command(resume={"approved": True}), config=config)
    assert resumed.get("human_confirm") is True


def test_human_confirm_skips_interrupt_without_high_risk():
    graph = _tiny_confirm_graph()
    config = {"configurable": {"thread_id": "human-confirm-2"}}
    state = {
        "goal": "g",
        "scope": "s",
        "issues_ledger": [
            {
                "id": "iss-2",
                "source": "qa",
                "risk": "low",
                "fix": "n/a",
                "evidence": "",
                "status": "open",
            }
        ],
        "qa_report": None,
        "human_confirm": None,
    }
    result = graph.invoke(state, config=config)  # type: ignore[arg-type]
    assert "__interrupt__" not in result
    assert result.get("human_confirm") is True


def _tiny_rehearsal_graph():
    builder = StateGraph(WorkflowState)
    builder.add_node("rehearsal_gate", rehearsal_gate)
    builder.add_edge(START, "rehearsal_gate")
    builder.add_edge("rehearsal_gate", END)
    return builder.compile(checkpointer=MemorySaver())


def test_rehearsal_gate_interrupt_then_resume():
    graph = _tiny_rehearsal_graph()
    config = {"configurable": {"thread_id": "rehearsal-1"}}
    state = {
        "goal": "g",
        "demo_artifact": {"artifact_path": "/tmp/demo", "version": "1"},
        "function_gate": {"pass": True},
        "visual_gate": {"pass": True, "unverified": False},
        "issues_ledger": [],
    }
    first = graph.invoke(state, config=config)  # type: ignore[arg-type]
    assert "__interrupt__" in first

    resumed = graph.invoke(
        Command(resume={"approved": True, "comment": "ok"}),
        config=config,
    )
    gate = resumed.get("rehearsal_gate") or {}
    assert gate.get("pass") is True
    assert gate.get("approved") is True


def test_review_subgraph_inherits_parent_checkpointer():
    """含 interrupt 的评审子图必须可继承父图 checkpointer（非 False）。"""
    review = build_review_graph()
    # CompiledStateGraph 在 checkpointer=None 时 checkpointer 属性为 None，
    # 作为子节点挂入父图后会继承父图 saver。
    assert getattr(review, "checkpointer", None) is None


def test_after_qa_reopen_twice_then_stops():
    """显式 reopen 两次后第三次应进入 human_confirm。"""
    state = {
        "qa_reopen_target": "produce",
        "loop_rerun_count": 0,
        "facts_ledger": [],
        "spec": None,
        "last_decided_facts_fp": "",
        "last_produced_spec_fp": "",
    }
    cmd1 = after_qa(state)  # type: ignore[arg-type]
    assert cmd1.goto == "produce"
    state = {**state, **(cmd1.update or {})}
    assert state["qa_reopen_target"] == ""
    assert state["loop_rerun_count"] == 1

    state["qa_reopen_target"] = "data"
    cmd2 = after_qa(state)  # type: ignore[arg-type]
    assert cmd2.goto == "data"
    state = {**state, **(cmd2.update or {})}
    assert state["loop_rerun_count"] == 2

    state["qa_reopen_target"] = "produce"
    cmd3 = after_qa(state)  # type: ignore[arg-type]
    assert cmd3.goto == "human_confirm"
