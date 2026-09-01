"""剩余审查项：真实截图、向量 embedding、Pydantic、skills、State Bus 裁剪。"""

from pathlib import Path

from codepilot.core.agent_loader import load_agent_harness
from codepilot.core.checkpointer import create_checkpointer
from codepilot.core.config import settings
from codepilot.core.context_views import slice_state
from codepilot.nodes.production_steps import compare
from codepilot.nodes.review_steps import visual_gate
from codepilot.states.entries import make_fact
from codepilot.states.models import FactEntryModel, validate_harness_output
from codepilot.tools.vector_memory import embed_text, vector_memory


def test_compare_screenshots_html_not_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    goal = "html_compare_demo"
    out_dir = tmp_path / goal
    out_dir.mkdir()
    markup = (
        "<!doctype html><html><body style='background:#ffffff'>"
        "<h1>Demo</h1><ul><li>A</li></ul></body></html>"
    )
    (out_dir / "index.html").write_text(markup, encoding="utf-8")
    result = compare({"goal": goal, "design_draft": {"components": ["A"], "layout": "list"}})  # type: ignore[arg-type]
    visual = result["visual_compare"]
    assert visual["mode"] in {"html_raster", "browser"}
    assert visual["unverified"] is False
    assert (out_dir / "design.html").exists()
    assert (out_dir / "design.png").exists()
    assert (out_dir / "screenshot.png").exists()


def test_visual_gate_accepts_html_raster_pass():
    result = visual_gate(  # type: ignore[arg-type]
        {
            "demo_artifact": {"artifact_path": "unused/build.zip"},
            "visual_compare": {
                "mode": "html_raster",
                "pass": True,
                "similarity": 0.99,
                "unverified": False,
            },
            "issues_ledger": [],
        }
    )
    assert result["checkpoints"] == ["VISUAL_GATE_PASS"]
    assert result["visual_gate"]["unverified"] is False


def test_vector_memory_stores_embedding_and_cosine_search(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vector_store_url", str(tmp_path / "vector_store.json"))
    added = vector_memory.invoke(
        {
            "action": "add",
            "collection": "project_memory",
            "data": {"id": "f1", "text": "经营周报点击率口径"},
        }
    )
    assert added is True
    hits = vector_memory.invoke(
        {
            "action": "search",
            "collection": "project_memory",
            "query": "周报点击率",
            "top_k": 3,
        }
    )
    assert hits
    assert hits[0]["id"] == "f1"
    assert "embedding" not in hits[0]
    assert hits[0]["score"] > 0
    assert len(embed_text("hello")) == 128


def test_sqlite_checkpointer_writes_checkpoints_dir(tmp_path, monkeypatch):
    db_path = tmp_path / "checkpoints" / "main.sqlite"
    monkeypatch.setattr(settings, "checkpoint_backend", "sqlite")
    monkeypatch.setattr(settings, "checkpoint_sqlite_path", str(db_path))
    for key in ("LANGGRAPH_API", "LANGGRAPH_RUNTIME", "LANGSMITH_LANGGRAPH_API_VARIANT"):
        monkeypatch.delenv(key, raising=False)
    saver = create_checkpointer()
    assert saver is not None
    assert db_path.exists()


def test_fact_entry_is_pydantic_validated():
    fact = make_fact(source="query_sql", metric="gmv", definition="成交额", value="1")
    loaded = FactEntryModel.model_validate(fact)
    assert loaded.metric == "gmv"
    validated = validate_harness_output(
        "producer_agent",
        {"goal": "str", "scope": "str", "constraints": "list"},
        {"goal": "做周报"},
    )
    assert validated["goal"] == "做周报"
    assert "scope" in validated


def test_slice_state_does_not_dump_foreign_ledgers():
    load_agent_harness.cache_clear()
    view = slice_state(
        {
            "goal": "g",
            "scope": "s",
            "facts_ledger": [{"id": "1"}],
            "issues_ledger": [{"id": "2"}],
            "demo_artifact": {"artifact_path": "x"},
        },
        "producer",
    )
    assert "goal" in view
    assert "facts_ledger" in view
    assert "issues_ledger" not in view
    assert "demo_artifact" not in view


def test_skills_directory_exists():
    skills = Path(__file__).resolve().parents[1] / "skills"
    for name in ("search_km.py", "query_sql.py", "screenshot_diff.py", "deploy_demo.py"):
        assert (skills / name).exists(), name
