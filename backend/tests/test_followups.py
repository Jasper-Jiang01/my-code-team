"""后续能力：闭环 rerun、锦标赛、PythonREPL / MCP、评测集。"""

from pathlib import Path

from langgraph.types import Command

from codepilot.graphs.decision import build_decision_graph
from codepilot.graphs.main_workflow import build_main_workflow
from codepilot.nodes.loop_control import after_qa, route_after_qa
from codepilot.nodes.tournament import filter_candidates, tournament
from codepilot.states.entries import make_fact
from codepilot.states.fingerprints import fingerprint_facts, fingerprint_spec
from codepilot.tools.mcp_call import mcp_call
from codepilot.tools.python_repl import python_repl


def test_facts_fingerprint_ignores_order():
    a = [make_fact(source="km", metric="m1", value="1"), make_fact(source="sql", metric="m2", value="2")]
    b = list(reversed(a))
    assert fingerprint_facts(a) == fingerprint_facts(b)


def test_route_after_qa_reruns_data_when_facts_change():
    facts = [make_fact(source="km", metric="gmv", value="1")]
    state = {
        "facts_ledger": facts,
        "spec": {"goal": "g", "scope": "s", "constraints": []},
        "last_decided_facts_fp": "old",
        "last_produced_spec_fp": fingerprint_spec({"goal": "g", "scope": "s", "constraints": []}),
        "loop_rerun_count": 0,
        "qa_reopen_target": "",
    }
    assert route_after_qa(state) == "data"  # type: ignore[arg-type]


def test_route_after_qa_reruns_produce_when_spec_changes():
    facts = [make_fact(source="km", metric="gmv", value="1")]
    spec = {"goal": "new", "scope": "s", "constraints": []}
    state = {
        "facts_ledger": facts,
        "spec": spec,
        "last_decided_facts_fp": fingerprint_facts(facts),
        "last_produced_spec_fp": "old-spec",
        "loop_rerun_count": 0,
        "qa_reopen_target": "",
    }
    assert route_after_qa(state) == "produce"  # type: ignore[arg-type]


def test_route_after_qa_confirms_when_fingerprints_match():
    facts = [make_fact(source="km", metric="gmv", value="1")]
    spec = {"goal": "g", "scope": "s", "constraints": []}
    state = {
        "facts_ledger": facts,
        "spec": spec,
        "last_decided_facts_fp": fingerprint_facts(facts),
        "last_produced_spec_fp": fingerprint_spec(spec),
        "loop_rerun_count": 0,
        "qa_reopen_target": "",
    }
    assert route_after_qa(state) == "human_confirm"  # type: ignore[arg-type]


def test_route_after_qa_stops_at_max_reruns():
    state = {
        "facts_ledger": [make_fact(source="km", metric="gmv", value="1")],
        "spec": {"goal": "g", "scope": "s", "constraints": []},
        "last_decided_facts_fp": "stale",
        "loop_rerun_count": 2,
        "qa_reopen_target": "data",
    }
    assert route_after_qa(state) == "human_confirm"  # type: ignore[arg-type]


def test_route_after_qa_prefers_explicit_reopen_target():
    facts = [make_fact(source="km", metric="gmv", value="1")]
    spec = {"goal": "g", "scope": "s", "constraints": []}
    state = {
        "facts_ledger": facts,
        "spec": spec,
        "last_decided_facts_fp": fingerprint_facts(facts),
        "last_produced_spec_fp": fingerprint_spec(spec),
        "loop_rerun_count": 0,
        "qa_reopen_target": "produce",
    }
    assert route_after_qa(state) == "produce"  # type: ignore[arg-type]


def test_route_after_qa_skips_fingerprint_when_snapshot_empty():
    """classify 直达 qa 时 last_*_fp 为空，不得误触发 rerun。"""
    facts = [make_fact(source="km", metric="gmv", value="1")]
    spec = {"goal": "g", "scope": "s", "constraints": []}
    state = {
        "facts_ledger": facts,
        "spec": spec,
        "last_decided_facts_fp": "",
        "last_produced_spec_fp": "",
        "loop_rerun_count": 0,
        "qa_reopen_target": "",
    }
    assert route_after_qa(state) == "human_confirm"  # type: ignore[arg-type]


def test_after_qa_command_clears_target_and_increments_count():
    cmd = after_qa(  # type: ignore[arg-type]
        {
            "qa_reopen_target": "data",
            "loop_rerun_count": 0,
            "facts_ledger": [],
            "spec": None,
            "last_decided_facts_fp": "",
            "last_produced_spec_fp": "",
        }
    )
    assert isinstance(cmd, Command)
    assert cmd.goto == "data"
    assert cmd.update == {"qa_reopen_target": "", "loop_rerun_count": 1}


def test_after_qa_command_to_human_confirm_clears_target():
    facts = [make_fact(source="km", metric="gmv", value="1")]
    spec = {"goal": "g", "scope": "s", "constraints": []}
    cmd = after_qa(  # type: ignore[arg-type]
        {
            "qa_reopen_target": "",
            "loop_rerun_count": 0,
            "facts_ledger": facts,
            "spec": spec,
            "last_decided_facts_fp": fingerprint_facts(facts),
            "last_produced_spec_fp": fingerprint_spec(spec),
        }
    )
    assert isinstance(cmd, Command)
    assert cmd.goto == "human_confirm"
    assert cmd.update == {"qa_reopen_target": ""}


def test_tournament_prefers_proposal_with_more_constraints():
    weak = {"id": "weak", "goal": "g", "scope": "s", "constraints": []}
    strong = {"id": "strong", "goal": "g", "scope": "s", "constraints": ["c1", "c2", "c3"]}
    result = tournament(  # type: ignore[arg-type]
        {
            "decision_shortlist": [weak, strong],
            "facts_ledger": [],
            "rules_ledger": [],
        }
    )
    assert result["decision_proposal"]["id"] == "strong"


def test_filter_candidates_drops_empty_shells():
    result = filter_candidates(  # type: ignore[arg-type]
        {
            "decision_candidates": [
                {"id": "bad", "goal": "g"},
                {"id": "good", "goal": "g", "scope": "商家周报", "constraints": ["只读"]},
            ]
        }
    )
    ids = {item["id"] for item in result["decision_shortlist"]}
    assert ids == {"good"}


def test_python_repl_writes_only_inside_workdir(tmp_path: Path):
    code = "f = open('hello.txt', 'w')\nf.write('ok')\nf.close()\nresult = 'hello.txt'\n"
    out = python_repl.invoke({"code": code, "workdir": str(tmp_path)})
    assert out["ok"] is True
    assert (tmp_path / "hello.txt").read_text() == "ok"


def test_python_repl_rejects_import():
    try:
        python_repl.invoke({"code": "import os\nresult = os.getcwd()", "workdir": "/tmp"})
        raise AssertionError("expected import to be rejected")
    except Exception as exc:  # noqa: BLE001
        assert "disallowed" in str(exc).lower() or "import" in str(exc).lower()


def test_mcp_lists_local_tools():
    catalog = mcp_call.invoke({"method": "tools/list"})
    names = {item["name"] for item in catalog["tools"]}
    assert {"python_repl", "deploy_demo", "query_sql", "pde_prototype"} <= names


def test_graphs_compile_with_new_nodes():
    main = build_main_workflow()
    assert "mark_decision" in main.nodes
    assert "mark_production" in main.nodes
    assert "after_qa" in main.nodes
    decision = build_decision_graph()
    assert "tournament" in decision.nodes
    assert "candidate_producer" in decision.nodes
