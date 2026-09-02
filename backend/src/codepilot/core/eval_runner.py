"""Harness 评测集：对 agents/*.yaml 做结构与契约回归（默认不调用 LLM）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codepilot.core.agent_loader import AgentHarness, _AGENTS_DIR, _BACKEND_ROOT, _TOOL_REGISTRY, load_agent_harness


@dataclass
class EvalResult:
    harness: str
    case_id: str
    passed: bool
    message: str


def list_harness_refs(agents_dir: Path | None = None) -> list[str]:
    root = agents_dir or _AGENTS_DIR
    refs: list[str] = []
    for path in sorted(root.rglob("*.yaml")):
        refs.append(str(path.relative_to(root).with_suffix("")).replace("\\", "/"))
    return refs


def _check_schema_keys(harness: AgentHarness, case: dict[str, Any]) -> str | None:
    expected = [str(item) for item in (case.get("keys") or [])]
    missing = [key for key in expected if key not in harness.output_schema]
    if missing:
        return f"missing output_schema keys: {missing}"
    return None


def _check_tools_exact(harness: AgentHarness, case: dict[str, Any]) -> str | None:
    expected = list(case.get("tools") or [])
    if list(harness.tool_names) != expected:
        return f"tools {harness.tool_names} != {expected}"
    return None


def _check_tools_contains(harness: AgentHarness, case: dict[str, Any]) -> str | None:
    expected = list(case.get("tools") or [])
    missing = [name for name in expected if name not in harness.tool_names]
    if missing:
        return f"missing tools: {missing}"
    return None


def _check_prompt_contains(harness: AgentHarness, case: dict[str, Any]) -> str | None:
    needles = [str(item) for item in (case.get("contains") or [])]
    missing = [text for text in needles if text not in harness.system_prompt]
    if missing:
        return f"prompt missing: {missing}"
    return None


def _check_prompt_forbids(harness: AgentHarness, case: dict[str, Any]) -> str | None:
    needles = [str(item) for item in (case.get("forbids") or [])]
    hit = [text for text in needles if text in harness.system_prompt]
    if hit:
        return f"prompt contains forbidden: {hit}"
    return None


def _check_skills_layout(_harness: AgentHarness, _case: dict[str, Any]) -> str | None:
    skills_dir = _BACKEND_ROOT / "skills"
    required = (
        "search_km.py",
        "query_sql.py",
        "screenshot_diff.py",
        "deploy_demo.py",
        "pde_prototype.py",
    )
    if not skills_dir.is_dir():
        return f"missing skills directory: {skills_dir}"
    missing = [name for name in required if not (skills_dir / name).exists()]
    if missing:
        return f"missing skills files: {missing}"
    return None


def _check_tools_registered(harness: AgentHarness, _case: dict[str, Any]) -> str | None:
    unknown = [name for name in harness.tool_names if name not in _TOOL_REGISTRY]
    if unknown:
        return f"unknown tools: {unknown}"
    return None


_CHECKERS = {
    "schema_keys": _check_schema_keys,
    "tools_exact": _check_tools_exact,
    "tools_contains": _check_tools_contains,
    "prompt_contains": _check_prompt_contains,
    "prompt_forbids": _check_prompt_forbids,
    "tools_registered": _check_tools_registered,
    "skills_layout": _check_skills_layout,
}


def run_harness_eval(harness_ref: str) -> list[EvalResult]:
    harness = load_agent_harness(harness_ref)
    cases = list(harness.eval_cases)
    if harness_ref.startswith("review_panels/") and not any(
        case.get("id") == "panel_veto" for case in cases
    ):
        cases.append({"id": "panel_veto", "type": "prompt_contains", "contains": ["否决点"]})
    if not any(case.get("type") == "tools_registered" for case in cases):
        cases.append({"id": "tools_registered", "type": "tools_registered"})
    if harness_ref == "research" and not any(case.get("type") == "skills_layout" for case in cases):
        cases.append({"id": "skills_layout", "type": "skills_layout"})
    results: list[EvalResult] = []
    for case in cases:
        case_id = str(case.get("id") or case.get("type") or "unnamed")
        case_type = str(case.get("type") or "")
        checker = _CHECKERS.get(case_type)
        if checker is None:
            results.append(
                EvalResult(harness_ref, case_id, False, f"unknown eval type: {case_type}")
            )
            continue
        message = checker(harness, case)
        results.append(EvalResult(harness_ref, case_id, message is None, message or "ok"))
    return results


def run_all_evals() -> list[EvalResult]:
    results: list[EvalResult] = []
    for ref in list_harness_refs():
        results.extend(run_harness_eval(ref))
    return results


def eval_summary(results: list[EvalResult] | None = None) -> tuple[int, int]:
    rows = results if results is not None else run_all_evals()
    passed = sum(1 for row in rows if row.passed)
    return passed, len(rows)
