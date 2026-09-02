"""ReviewGraph 评审阶段的节点实现。

根据技术方案第 3.1 / 7 节，评审阶段包含：
- 五岗位评委并行评审；
- 三道门禁严格串联：功能门 -> 视觉门 -> 演示门；
- ``fix_agent`` 证据驱动修复，修复后回到功能门；
- ``loop_condition`` 判定 ``issues_ledger`` 是否仍有高风险问题。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TypedDict

from langgraph.types import Send, interrupt

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.context_views import format_state_context
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.entries import make_issue, resolve_issues
from codepilot.states.workflow_state import IssueEntry, QAReport, WorkflowState
from codepilot.tools import screenshot_diff

logger = logging.getLogger(__name__)

_REVIEW_PANELS = (
    "review_panels/platform",
    "review_panels/assets",
    "review_panels/user_poi",
    "review_panels/merchant",
    "review_panels/city_supply",
)

_MAX_FIX_ROUNDS = 3


class PanelInput(TypedDict):
    """单次 ``Send("panel", ...)`` 分发的输入载荷。"""

    panel_ref: str
    demo_artifact: dict | None
    spec: dict | None


def _parse_json(raw: str, default: dict | None = None) -> dict:
    parsed = extract_json(raw)
    if isinstance(parsed, dict):
        return parsed
    return dict(default or {})


def _issues_from_texts(
    source: str,
    texts: list,
    *,
    risk: str,
    evidence: str = "",
) -> list[IssueEntry]:
    issues: list[IssueEntry] = []
    for text in texts:
        message = str(text).strip()
        if not message:
            continue
        issues.append(
            make_issue(source=source, message=message, risk=risk, evidence=evidence)
        )
    return issues


def _open_high_risk(issues: list[IssueEntry]) -> bool:
    return any(
        issue.get("risk") == "high" and issue.get("status") != "resolved" for issue in issues
    )


# -- 五岗位评委 fan-out ---------------------------------------------------


def review_fan_out(state: WorkflowState) -> list[Send] | str:
    """为每个评委岗位分发一个 ``panel`` 任务以并行执行。"""
    round_count = state.get("review_round", 0)
    if round_count > 0:
        return "function_gate"

    demo = state.get("demo_artifact")
    spec = state.get("spec")
    payload_base = {"demo_artifact": demo, "spec": spec}
    return [Send("panel", {**payload_base, "panel_ref": ref}) for ref in _REVIEW_PANELS]


def panel(payload: PanelInput) -> dict:
    """单个评委岗位的评审逻辑。结论同时写入 issues_ledger。"""
    panel_ref = payload.get("panel_ref", "")
    if not panel_ref:
        return {"review_panel_results": [], "review_issues": [], "issues_ledger": []}

    demo = payload.get("demo_artifact") or {}
    spec = payload.get("spec") or {}
    task = (
        "请评审以下 Demo 产物，从你的否决点角度给出结论。必须引用产物路径或规格中的证据。\n"
        f"{format_state_context({'demo_artifact': demo, 'spec': spec}, panel_ref)}\n\n"
        '只输出 JSON，格式为: {"verdict": "pass"|"needs_fix"|"reject", "issues": ["问题1"]}'
    )

    result: dict = {"id": panel_ref, "panel": panel_ref, "verdict": "pass", "issues": []}
    try:
        response = invoke_agent(panel_ref, task)
        parsed = _parse_json(safe_content(response))
        if parsed:
            result.update(parsed)
            result["id"] = panel_ref
            result["panel"] = panel_ref
    except Exception:  # noqa: BLE001
        logger.exception("panel: failed for panel_ref=%r", panel_ref)
        result["verdict"] = "needs_fix"
        result["issues"] = [f"评委 {panel_ref} 执行失败"]

    verdict = result.get("verdict", "pass")
    risk = "high" if verdict == "reject" else "medium"
    evidence = json.dumps({"demo": demo, "verdict": verdict}, ensure_ascii=False)
    issues = _issues_from_texts(
        panel_ref,
        result.get("issues") or [],
        risk=risk,
        evidence=evidence,
    )
    return {
        "review_panel_results": [result],
        "review_issues": issues,
        "issues_ledger": issues,
    }


# -- 三道门禁 -------------------------------------------------------------


def function_gate(state: WorkflowState) -> dict:
    """功能门 — 先做产物存在性等确定性检查，再用 QA Agent 做路径检查。"""
    demo = state.get("demo_artifact") or {}
    artifact_path = str(demo.get("artifact_path") or "")
    evidence_bits: list[str] = []
    issue_texts: list[str] = []
    passed = True

    if not artifact_path:
        passed = False
        issue_texts.append("Demo 产物路径缺失")
    else:
        path = Path(artifact_path)
        if not path.exists():
            passed = False
            issue_texts.append(f"Demo 产物文件不存在: {artifact_path}")
        else:
            evidence_bits.append(f"artifact_exists={artifact_path}")

    audit = state.get("design_audit") or {}
    if audit and not audit.get("approved"):
        passed = False
        issue_texts.append("设计 GUARD 未批准，功能门拒绝放行")
        evidence_bits.append(json.dumps(audit.get("issues") or [], ensure_ascii=False))

    llm_gate: dict = {}
    try:
        response = invoke_agent(
            "qa",
            (
                "请执行功能门检查：关键路径完整性、返回关系、状态一致性。"
                "必须引用 Demo 路径，禁止在没有证据时输出 pass=true。\n"
                f"Demo: {json.dumps(demo, ensure_ascii=False)}\n\n"
                '只输出 JSON: {"pass": bool, "issues": ["问题1"]}'
            ),
        )
        llm_gate = _parse_json(safe_content(response))
        if llm_gate.get("pass") is False:
            passed = False
        for text in llm_gate.get("issues") or []:
            issue_texts.append(str(text))
    except Exception:  # noqa: BLE001
        logger.exception("function_gate: QA agent failed")
        passed = False
        issue_texts.append("功能门 QA Agent 执行失败")

    evidence = "; ".join(evidence_bits) or artifact_path or "no_artifact"
    if passed:
        issues = resolve_issues(state.get("issues_ledger") or [], source="function_gate")
    else:
        issues = _issues_from_texts("function_gate", issue_texts, risk="high", evidence=evidence)

    gate = {"pass": passed, "issues": issue_texts, "evidence": evidence, **{k: v for k, v in llm_gate.items() if k not in {"pass", "issues"}}}
    return {
        "function_gate": gate,
        "review_issues": issues,
        "issues_ledger": issues,
        "checkpoints": ["FUNCTION_GATE_PASS" if passed else "FUNCTION_GATE_FAIL"],
    }


def visual_gate(state: WorkflowState) -> dict:
    """视觉门 — 占位图不得视为通过；真实 HTML/浏览器对比按相似度判定。"""
    demo = state.get("demo_artifact") or {}
    existing = state.get("visual_compare") or {}
    artifact_path = str(demo.get("artifact_path") or "")

    if existing.get("mode") == "placeholder" or (
        existing.get("unverified") and existing.get("mode") not in {"browser", "html_raster"}
    ):
        message = "视觉对比使用占位图，未验证真实还原"
        issues = [
            make_issue(
                source="visual_gate",
                message=message,
                risk="medium",
                evidence=json.dumps(existing, ensure_ascii=False),
            )
        ]
        gate = {**existing, "pass": False, "unverified": True, "issues": [message]}
        return {
            "visual_gate": gate,
            "review_issues": issues,
            "issues_ledger": issues,
            "checkpoints": ["VISUAL_GATE_UNVERIFIED"],
        }

    compare_result: dict = {"pass": False, "similarity": 0.0, "issues": []}
    if existing.get("mode") in {"browser", "html_raster"}:
        compare_result = {
            "pass": bool(existing.get("pass", False)),
            "similarity": existing.get("similarity", 0.0),
            "diff_image_path": existing.get("diff_image_path"),
            "mode": existing.get("mode"),
            "unverified": False,
            "issues": []
            if existing.get("pass")
            else [f"视觉还原相似度 {existing.get('similarity', 0.0)} 低于阈值"],
        }
    else:
        reference_path = artifact_path.replace("build.zip", "design.png") if artifact_path else ""
        actual_path = artifact_path.replace("build.zip", "screenshot.png") if artifact_path else ""
        try:
            result = screenshot_diff.invoke(
                {
                    "reference_path": reference_path,
                    "actual_path": actual_path,
                    "threshold": 0.95,
                }
            )
            compare_result["pass"] = bool(result.get("pass", False))
            compare_result["similarity"] = result.get("similarity", 0.0)
            compare_result["diff_image_path"] = result.get("diff_image_path")
            compare_result["mode"] = "file"
            compare_result["unverified"] = False
            if not compare_result["pass"]:
                compare_result["issues"] = [
                    f"视觉还原相似度 {compare_result['similarity']} 低于阈值"
                ]
        except Exception:  # noqa: BLE001
            logger.exception("visual_gate: screenshot_diff failed")
            compare_result["issues"] = ["截图对比工具执行失败"]

    if compare_result["pass"]:
        issues = resolve_issues(state.get("issues_ledger") or [], source="visual_gate")
    else:
        issues = _issues_from_texts(
            "visual_gate",
            compare_result.get("issues") or [],
            risk="medium",
            evidence=json.dumps(compare_result, ensure_ascii=False),
        )
    return {
        "visual_gate": compare_result,
        "review_issues": issues,
        "issues_ledger": issues,
        "checkpoints": ["VISUAL_GATE_PASS" if compare_result["pass"] else "VISUAL_GATE_FAIL"],
    }


def rehearsal_gate(state: WorkflowState) -> dict:
    """演示门 — interrupt 等待人工彩排确认。

    功能门未过时跳过 interrupt，直接返回失败。
    """
    demo = state.get("demo_artifact") or {}
    function_pass = bool((state.get("function_gate") or {}).get("pass"))
    visual = state.get("visual_gate") or {}
    visual_pass = bool(visual.get("pass")) and not visual.get("unverified")

    # 功能门或视觉门未过时，跳过人工彩排
    if not function_pass or not visual_pass:
        issue_texts = []
        if not function_pass:
            issue_texts.append("功能门未通过，演示门不得放行")
        if not visual_pass:
            issue_texts.append("视觉门未通过，演示门不得放行")
        issues = _issues_from_texts(
            "rehearsal_gate",
            issue_texts,
            risk="high",
            evidence=json.dumps({"function_pass": function_pass, "visual_pass": visual_pass}),
        )
        gate = {"pass": False, "approved": False, "comment": "auto-skipped: gates not passed", "issues": issue_texts}
        return {
            "rehearsal_gate": gate,
            "review_issues": issues,
            "issues_ledger": issues,
            "checkpoints": ["REHEARSAL_GATE_SKIP"],
        }

    decision = interrupt(
        {
            "reason": "rehearsal_gate",
            "demo_artifact": demo,
            "function_gate": state.get("function_gate"),
            "visual_gate": visual,
            "issues_ledger": state.get("issues_ledger", []),
            "prompt": (
                "请完成演示门彩排（设备/网络/兜底）。"
                "通过 Command(resume={'approved': true|false, 'comment': '...'} ) 恢复。"
            ),
        }
    )

    approved = False
    comment = ""
    if isinstance(decision, dict):
        approved = bool(decision.get("approved", False))
        comment = str(decision.get("comment") or "")
    else:
        approved = bool(decision)

    # 功能门未过时，即使人工想放行也不把演示门记为通过。
    passed = approved and function_pass
    evidence = json.dumps(
        {"approved": approved, "comment": comment, "function_pass": function_pass},
        ensure_ascii=False,
    )
    if passed:
        issues = resolve_issues(state.get("issues_ledger") or [], source="rehearsal_gate")
        issue_texts: list[str] = []
    else:
        issue_texts = [comment or "演示门未通过人工彩排"]
        if not function_pass:
            issue_texts.append("功能门未通过，演示门不得放行")
        issues = _issues_from_texts(
            "rehearsal_gate",
            issue_texts,
            risk="high",
            evidence=evidence,
        )

    gate = {"pass": passed, "approved": approved, "comment": comment, "issues": issue_texts}
    return {
        "rehearsal_gate": gate,
        "review_issues": issues,
        "issues_ledger": issues,
        "checkpoints": ["REHEARSAL_GATE_PASS" if passed else "REHEARSAL_GATE_FAIL"],
    }


# -- fix_agent 与 loop_condition -------------------------------------------


def fix_agent(state: WorkflowState) -> dict:
    """证据驱动修复：针对未解决问题改产物，并把问题标为 processing。"""
    round_count = int(state.get("review_round") or 0) + 1
    issues = list(state.get("issues_ledger") or [])
    open_issues = [issue for issue in issues if issue.get("status") != "resolved"]

    demo = dict(state.get("demo_artifact") or {})
    fix_notes = list(demo.get("fix_notes") or [])
    try:
        response = invoke_agent(
            "qa",
            (
                "请基于下列问题与证据驱动下一轮修复。不要宣称已通过门禁。\n"
                "若问题根因是事实/数据缺失或错误（需重跑 Decision/数据阶段），"
                "设 reopen_target=\"data\"；若根因是规格缺陷（需重跑"
                " Production），设 reopen_target=\"produce\"；若可在产物层"
                "修复则留空。\n"
                '输出 JSON: {"fix_notes": ["改动1"], "resolved_ids": [], '
                '"reopen_target": "data"|"produce"|""}.\n'
                f"{format_state_context(state, 'qa')}\n"
                f"open_issues: {json.dumps(open_issues, ensure_ascii=False)}"
            ),
        )
        parsed = _parse_json(safe_content(response))
        for note in parsed.get("fix_notes") or []:
            fix_notes.append(str(note))
        # QA 显式回写重跑目标，供 route_after_qa 读取
        reopen = str(parsed.get("reopen_target") or "").strip().lower()
        if reopen in {"data", "produce"}:
            qa_reopen_target = reopen
        else:
            qa_reopen_target = ""
    except Exception:  # noqa: BLE001
        logger.exception("fix_agent: QA agent failed")
        fix_notes.append(f"round {round_count}: 自动修复调用失败，保留问题等待重跑门禁")
        qa_reopen_target = ""

    demo["fix_notes"] = fix_notes
    demo["version"] = f"fix-round-{round_count}"

    updated = [
        {**issue, "status": "processing"}
        for issue in open_issues
        if issue.get("id")
    ]
    # 保留已 resolved 的旧问题，避免丢失状态
    resolved_issues = [
        issue for issue in issues
        if issue.get("status") == "resolved" or not issue.get("id")
    ]
    all_issues = resolved_issues + updated
    logger.info("fix_agent: round %d, processing %d open issues", round_count, len(updated))
    return {
        "review_round": round_count,
        "demo_artifact": demo,
        "issues_ledger": all_issues,
        "qa_reopen_target": qa_reopen_target,
        "checkpoints": [f"FIX_ROUND_{round_count}"],
    }


def loop_condition(state: WorkflowState) -> str:
    """判定三道门禁后是否仍有高风险问题需要修复。"""
    round_count = int(state.get("review_round") or 0)
    if round_count >= _MAX_FIX_ROUNDS:
        logger.warning("loop_condition: max fix rounds (%d) reached, force-done", _MAX_FIX_ROUNDS)
        return "done"

    issues = state.get("issues_ledger") or []
    return "fix" if _open_high_risk(issues) else "done"


def finalize_review(state: WorkflowState) -> dict:
    """汇总三道门禁结果。不把未修复问题标为 resolved。"""
    func_gate = state.get("function_gate") or {}
    vis_gate = state.get("visual_gate") or {}
    reh_gate = state.get("rehearsal_gate") or {}

    function_pass = bool(func_gate.get("pass", False))
    visual_pass = bool(vis_gate.get("pass", False)) and not vis_gate.get("unverified")
    rehearsal_pass = bool(reh_gate.get("pass", False))

    issues = list(state.get("issues_ledger") or [])
    all_gates_pass = function_pass and visual_pass and rehearsal_pass
    no_high_risk = not _open_high_risk(issues)

    qa_report: QAReport = {
        "function_pass": function_pass,
        "visual_pass": visual_pass,
        "rehearsal_pass": rehearsal_pass,
        "issues": issues,
    }

    try:
        from codepilot.core.memory_store import update_agent_memory

        update_agent_memory(
            "qa_agent",
            last_gate_pass=all_gates_pass and no_high_risk,
            last_issue_count=len(issues),
        )
    except Exception:  # noqa: BLE001
        logger.exception("finalize_review: failed to persist agent memory")

    checkpoint = "QA_PASS" if all_gates_pass and no_high_risk else "QA_FAIL"
    return {
        "qa_report": qa_report,
        "checkpoints": [checkpoint],
    }
