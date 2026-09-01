"""按 Harness 裁剪 State Bus，避免把整份台账 dump 进每个 Agent。"""

from __future__ import annotations

import json
from typing import Any, Mapping

from codepilot.core.agent_loader import load_agent_harness

_DEFAULT_FIELDS: dict[str, tuple[str, ...]] = {
    "research": ("goal", "scope"),
    "data": ("goal", "scope", "facts_ledger"),
    "producer": ("goal", "scope", "facts_ledger", "rules_ledger", "decision_critique"),
    "critic": ("decision_proposal", "facts_ledger", "rules_ledger"),
    "design": ("spec", "evidence", "rules_ledger", "design_draft", "design_audit"),
    "guard": ("spec", "design_draft", "rules_ledger"),
    "qa": ("demo_artifact", "spec", "issues_ledger", "function_gate", "visual_gate"),
}


def slice_state(state: Mapping[str, Any], harness_ref: str) -> dict[str, Any]:
    """只返回该 Harness 声明（或默认）允许读取的字段。"""
    try:
        harness = load_agent_harness(harness_ref)
        declared = list(harness.state_fields or ())
    except Exception:  # noqa: BLE001
        declared = []
    key = harness_ref.split("/")[-1].replace(".yaml", "")
    fields = declared or list(_DEFAULT_FIELDS.get(key, ()))
    if harness_ref.startswith("review_panels/") and not fields:
        fields = ["demo_artifact", "spec"]
    if not fields:
        return {
            k: state.get(k)
            for k in ("goal", "scope")
            if k in state
        }
    return {field: state.get(field) for field in fields}


def format_state_context(state: Mapping[str, Any], harness_ref: str) -> str:
    return json.dumps(slice_state(state, harness_ref), ensure_ascii=False, default=str)
