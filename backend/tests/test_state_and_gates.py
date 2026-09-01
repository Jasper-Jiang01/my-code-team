"""P0/P1 修复的回归测试：证据落盘、台账 upsert、质检回环。"""

from codepilot.graphs.production import _after_guard
from codepilot.nodes.researcher import researcher
from codepilot.nodes.review_steps import finalize_review, loop_condition
from codepilot.nodes.route_task import route_task
from codepilot.states.entries import make_fact, make_issue, resolve_issues
from codepilot.states.reducers import unique_extend, upsert_by_id
from codepilot.tools.query_sql import query_sql
from codepilot.tools.search_km import search_km


def test_upsert_by_id_updates_status_without_duplicating():
    left = [make_issue(source="function_gate", message="路径断裂", risk="high")]
    right = [{**left[0], "status": "resolved"}]
    merged = upsert_by_id(left, right)
    assert len(merged) == 1
    assert merged[0]["status"] == "resolved"


def test_unique_extend_is_idempotent():
    assert unique_extend(["SPEC_LOCKED"], ["SPEC_LOCKED", "EVIDENCE_READY"]) == [
        "SPEC_LOCKED",
        "EVIDENCE_READY",
    ]


def test_resolve_issues_only_patches_matching_source():
    issues = [
        make_issue(source="function_gate", message="a", risk="high"),
        make_issue(source="visual_gate", message="b", risk="medium"),
    ]
    patches = resolve_issues(issues, source="function_gate")
    assert len(patches) == 1
    assert patches[0]["source"] == "function_gate"
    assert patches[0]["status"] == "resolved"


def test_researcher_persists_km_evidence_instead_of_query_only():
    result = researcher({"query": "经营周报点击率口径"})
    findings = result["research_findings"]
    assert findings, "search_km fixture should yield evidence"
    for fact in findings:
        assert fact["source"] != "no_result"
        assert fact.get("value") or fact.get("snippet")
        assert fact["metric"] == "经营周报点击率口径"


def test_search_km_fixture_has_source_url_snippet():
    rows = search_km.invoke({"query": "供给冷启动", "top_k": 2})
    assert len(rows) == 2
    assert rows[0]["source"] == "km_fixture"
    assert rows[0]["url"]
    assert rows[0]["snippet"]


def test_query_sql_fixture_returns_metrics_with_definition():
    rows = query_sql.invoke(
        {"sql": "SELECT metric, value FROM metrics WHERE context = 'weekly report'"}
    )
    assert rows
    assert {row["metric"] for row in rows} >= {"weekly_active_merchants", "report_click_rate"}
    assert all("definition" in row for row in rows)


def test_loop_condition_reads_issues_ledger_high_risk():
    open_high = {
        "issues_ledger": [make_issue(source="function_gate", message="控制台报错", risk="high")],
        "review_round": 0,
    }
    assert loop_condition(open_high) == "fix"  # type: ignore[arg-type]

    resolved = {
        "issues_ledger": [
            make_issue(source="function_gate", message="控制台报错", risk="high", status="resolved")
        ],
        "review_round": 0,
    }
    assert loop_condition(resolved) == "done"  # type: ignore[arg-type]


def test_finalize_review_does_not_mark_open_issues_resolved():
    issue = make_issue(source="rehearsal_gate", message="彩排失败", risk="high")
    result = finalize_review(  # type: ignore[arg-type]
        {
            "function_gate": {"pass": False},
            "visual_gate": {"pass": False, "unverified": True},
            "rehearsal_gate": {"pass": False},
            "issues_ledger": [issue],
        }
    )
    assert result["qa_report"]["issues"][0]["status"] == "open"
    assert result["checkpoints"] == ["QA_FAIL"]
    assert "issues_ledger" not in result


def test_route_task_rejects_unknown_next_step():
    assert route_task({"next_step": "unknown"}) == "research"  # type: ignore[arg-type]
    assert route_task({"next_step": "produce"}) == "produce"  # type: ignore[arg-type]


def test_make_fact_has_audit_fields():
    fact = make_fact(
        source="query_sql",
        metric="report_click_rate",
        definition="点击 UV / 触达 UV",
        value="0.23",
    )
    assert fact["id"]
    assert fact["definition"]
    assert fact["value"]
    assert fact["timestamp"]


def test_guard_rejects_loop_back_to_generate():
    assert (
        _after_guard({"design_audit": {"approved": False}, "production_guard_round": 1})
        == "generate"
    )
    assert (
        _after_guard({"design_audit": {"approved": False}, "production_guard_round": 3})
        == "build"
    )
    assert (
        _after_guard({"design_audit": {"approved": True}, "production_guard_round": 1})
        == "build"
    )
