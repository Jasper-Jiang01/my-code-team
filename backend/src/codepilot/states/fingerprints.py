"""用于闭环间 rerun 的状态指纹。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def fingerprint(value: Any) -> str:
    """对台账 / spec 做稳定哈希，供主图判断是否需要重跑下游。"""
    digest = hashlib.sha256(_stable_dump(value).encode("utf-8")).hexdigest()
    return digest[:16]


def fingerprint_facts(facts: list | None) -> str:
    rows = []
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": item.get("id"),
                "source": item.get("source"),
                "metric": item.get("metric"),
                "value": item.get("value"),
                "definition": item.get("definition"),
            }
        )
    rows.sort(key=lambda row: str(row.get("id") or ""))
    return fingerprint(rows)


def fingerprint_spec(spec: dict | None) -> str:
    if not spec:
        return ""
    constraints = sorted(spec.get("constraints") or [])
    return fingerprint(
        {
            "goal": spec.get("goal"),
            "scope": spec.get("scope"),
            "constraints": constraints,
        }
    )
