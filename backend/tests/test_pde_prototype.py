"""pde_prototype：本地原型、远端适配、生产 GENERATE 接入。"""

import importlib
from pathlib import Path

from codepilot.core.agent_loader import _TOOL_REGISTRY, load_agent_harness
from codepilot.core.config import settings
from codepilot.nodes.production_steps import generate
from codepilot.tools.pde_prototype import pde_prototype

pde_mod = importlib.import_module("codepilot.tools.pde_prototype")


def test_local_prototype_writes_html(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pde_endpoint", "")
    out = pde_prototype.invoke(
        {
            "requirement": "附近页增加热门美食瓷片",
            "page_name": "附近",
            "components": ["热门美食", "热门玩乐"],
            "output_dir": str(tmp_path),
        }
    )
    assert out["ok"] is True
    assert out["mode"] == "local_prototype"
    html_path = Path(out["html_path"])
    assert html_path.exists()
    markup = html_path.read_text(encoding="utf-8")
    assert "附近" in markup
    assert "热门美食" in markup
    assert "km.sankuai.com/collabpage/2776444575" in out["launch"]["guide"]
    assert "dfs.sankuai.com" in out["launch"]["df"]


def test_rejects_empty_requirement():
    try:
        pde_prototype.invoke({"requirement": "  "})
        raise AssertionError("expected empty requirement to fail")
    except Exception as exc:  # noqa: BLE001
        assert "requirement" in str(exc).lower()


def test_remote_endpoint_writes_html(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pde_endpoint", "https://pde.example/run")

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "html": "<!doctype html><html><body><h1>远程原型</h1><ul><li>底栏横幅</li></ul></body></html>",
                "task_url": "https://dfs.sankuai.com/task/1",
                "layout": "remote-card",
            }

    monkeypatch.setattr(pde_mod.httpx, "post", lambda *args, **kwargs: _Resp())
    out = pde_prototype.invoke(
        {
            "requirement": "美食商户页底部横幅",
            "output_dir": str(tmp_path),
        }
    )
    assert out["mode"] == "remote"
    assert out["task_url"].endswith("/task/1")
    assert Path(out["html_path"]).exists()
    assert "远程原型" in Path(out["html_path"]).read_text(encoding="utf-8")


def test_generate_uses_pde_prototype(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    monkeypatch.setattr(settings, "pde_endpoint", "")

    def _fake_agent(*_args, **_kwargs):
        raise AssertionError("prototype generate must not call LLM")

    monkeypatch.setattr("codepilot.nodes.production_steps.invoke_agent", _fake_agent)
    result = generate(
        {  # type: ignore[arg-type]
            "goal": "出附近页原型图",
            "design_draft": {"increments": ["瓷片"], "components": ["搜索框"]},
        }
    )
    draft = result["design_draft"]
    html_path = Path(tmp_path) / "出附近页原型图" / "design.html"
    assert html_path.exists()
    assert "搜索框" in html_path.read_text(encoding="utf-8")
    assert draft["prototype"]["html_path"] == str(html_path)
    assert result["checkpoints"] == ["GENERATE_DONE"]
    assert "文件：" in str(result.get("chitchat_reply") or "")


def test_tool_registered_on_design_agent():
    load_agent_harness.cache_clear()
    harness = load_agent_harness("design")
    assert "pde_prototype" in harness.tool_names
    assert "pde_prototype" in _TOOL_REGISTRY
