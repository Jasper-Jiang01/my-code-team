"""search_km：夹具、HTTP 适配层、学城 citadel。"""

from __future__ import annotations

import importlib
import json

from codepilot.core.config import settings
from codepilot.tools.search_km import search_km

# 必须 import_module：`codepilot.tools.search_km` 属性已被 StructuredTool 占用。
km_mod = importlib.import_module("codepilot.tools.search_km")


def test_search_km_fixture_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "km_search_endpoint", "")
    monkeypatch.setattr(settings, "km_mis", "")
    rows = search_km.invoke({"query": "供给冷启动", "top_k": 2})
    assert len(rows) == 2
    assert rows[0]["source"] == "km_fixture"
    assert rows[0]["url"]
    assert rows[0]["snippet"]


def test_search_km_citadel_maps_hits_and_strips_highlight(monkeypatch):
    monkeypatch.setattr(settings, "km_search_endpoint", "")
    monkeypatch.setattr(settings, "km_mis", "jiangwenzhe02")
    monkeypatch.setattr(settings, "km_fetch_body_top_k", 0)
    monkeypatch.setattr(settings, "km_snippet_max_chars", 200)

    payload = {
        "items": [
            {
                "contentId": "2776324445",
                "title": "境外休娱-按摩SPA品类货架方案",
                "contentBodyHl": "境外按摩/SPA品类是休闲娱乐业务的<b>核心增长</b>",
                "spaceName": "服务零售产品部",
            },
            {
                "contentId": "2746106889",
                "title": "样例文档二",
                "contentBodyHl": "口径说明",
                "spaceName": "点评",
            },
        ]
    }

    def fake_run(args, timeout):
        assert args[0] == "searchContent"
        assert "--keyword" in args
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(km_mod, "_run_citadel", fake_run)
    rows = search_km.invoke({"query": "按摩货架", "top_k": 2})
    assert len(rows) == 2
    assert rows[0]["url"] == "https://km.sankuai.com/collabpage/2776324445"
    assert rows[0]["source"] == "xuecheng:服务零售产品部"
    assert "<b>" not in rows[0]["snippet"]
    assert "核心增长" in rows[0]["snippet"]
    assert rows[1]["url"] == "https://km.sankuai.com/collabpage/2746106889"


def test_search_km_citadel_enriches_top_hits_with_markdown(monkeypatch):
    monkeypatch.setattr(settings, "km_search_endpoint", "")
    monkeypatch.setattr(settings, "km_mis", "jiangwenzhe02")
    monkeypatch.setattr(settings, "km_fetch_body_top_k", 1)
    monkeypatch.setattr(settings, "km_snippet_max_chars", 1500)

    search_payload = {
        "items": [
            {
                "contentId": "2776324445",
                "title": "境外休娱-按摩SPA品类货架方案",
                "contentBodyHl": "短摘要",
                "spaceName": "服务零售产品部",
            }
        ]
    }
    md = (
        "文档标题：货架方案\n"
        "============================================================\n"
        "文档内容（简化版，仅供阅读）：\n"
        "============================================================\n"
        "---\n"
        "\n"
        "# 境外休娱-按摩SPA品类货架方案\n"
        "\n"
        "境外按摩/SPA品类是休闲娱乐业务的核心增长，东南亚需求旺盛。\n"
    )

    def fake_run(args, timeout):
        if args[0] == "searchContent":
            return json.dumps(search_payload, ensure_ascii=False)
        if args[0] == "getSimpleMarkdown":
            assert args[2] == "2776324445"
            return md
        raise AssertionError(args)

    monkeypatch.setattr(km_mod, "_run_citadel", fake_run)
    rows = search_km.invoke({"query": "按摩SPA货架", "top_k": 1})
    assert rows[0]["source"].startswith("xuecheng")
    assert "核心增长" in rows[0]["snippet"]
    assert "简化版 Markdown" not in rows[0]["snippet"]
    assert rows[0]["snippet"] != "短摘要"


def test_search_km_citadel_error_does_not_fabricate_fixture(monkeypatch):
    monkeypatch.setattr(settings, "km_search_endpoint", "")
    monkeypatch.setattr(settings, "km_mis", "jiangwenzhe02")

    def boom(args, timeout):
        raise km_mod.KMSearchError("citadel down")

    monkeypatch.setattr(km_mod, "_run_citadel", boom)
    rows = search_km.invoke({"query": "任何问题", "top_k": 3})
    assert rows == []


def test_search_km_citadel_empty_hits_does_not_fabricate_fixture(monkeypatch):
    monkeypatch.setattr(settings, "km_search_endpoint", "")
    monkeypatch.setattr(settings, "km_mis", "jiangwenzhe02")
    monkeypatch.setattr(
        km_mod,
        "_run_citadel",
        lambda args, timeout: json.dumps({"items": [], "totalCount": 0}),
    )
    rows = search_km.invoke({"query": "不存在的口径xyz", "top_k": 3})
    assert rows == []
