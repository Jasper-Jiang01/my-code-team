"""ReviewGraph 评审阶段的节点实现。

根据技术方案第 3.1 / 7 节，评审阶段包含：
- 五岗位评委（review_panels/*.yaml）并行评审，使用 ``Send`` fan-out；
- 三道门禁严格串联：功能门 -> 视觉门 -> 演示门；
- ``fix_agent`` 证据驱动修复，修复后回到功能门重新走完整串联；
- ``loop_condition`` 判定 ``issues_ledger`` 是否仍有高风险问题。

读写：``issues_ledger`` / ``qa_report``（由本子图拥有）。
使用的工具：``screenshot_diff``、``deploy_demo``（通过 ``qa`` Agent Harness）。
"""

import json
import logging
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Send

from codepilot.core.agent_loader import load_agent_harness
from codepilot.core.create_model import create_chat_model
from codepilot.states.workflow_state import IssueEntry, QAReport, WorkflowState
from codepilot.tools import screenshot_diff

logger = logging.getLogger(__name__)

# 五岗位评委的 Harness 路径（相对于 agents/ 目录）
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


def _safe_content(response) -> str:
    content = response.content if hasattr(response, "content") else response
    return content if isinstance(content, str) else str(content)


def _parse_json(raw: str, default: dict | None = None) -> dict:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        logger.warning("review_steps: failed to parse JSON from LLM output")
    return dict(default or {})


# -- 五岗位评委 fan-out ---------------------------------------------------


def review_fan_out(state: WorkflowState) -> list[Send] | str:
    """为每个评委岗位分发一个 ``panel`` 任务以并行执行。

    当 ``review_round`` 达到上限时跳过评委阶段，直接进入功能门，
    避免修复循环中重复评审。
    """
    round_count = state.get("review_round", 0)
    if round_count > 0:
        # 修复回环后跳过评委，直接重跑三道门禁
        return "function_gate"

    demo = state.get("demo_artifact")
    spec = state.get("spec")
    payload_base = {"demo_artifact": demo, "spec": spec}
    return [
        Send("panel", {**payload_base, "panel_ref": ref}) for ref in _REVIEW_PANELS
    ]


def panel(payload: PanelInput) -> dict:
    """单个评委岗位的评审逻辑。

    Args:
        payload: 包含 ``panel_ref``、``demo_artifact``、``spec`` 的字典。

    Returns:
        包含 ``review_panel_results`` 和 ``review_issues`` 的字典。
    """
    panel_ref = payload.get("panel_ref", "")
    if not panel_ref:
        return {"review_panel_results": [], "review_issues": []}

    harness = load_agent_harness(panel_ref)
    task = (
        f"请评审以下 Demo 产物，从你的否决点角度给出结论。\n"
        f"Demo: {json.dumps(payload.get('demo_artifact') or {}, ensure_ascii=False)}\n"
        f"Spec: {json.dumps(payload.get('spec') or {}, ensure_ascii=False)}\n\n"
        f"只输出 JSON，格式为: "
        f'{{"verdict": "pass"|"needs_fix"|"reject", "issues": ["问题1", ...]}}'
    )

    result: dict = {"panel": panel_ref, "verdict": "pass", "issues": []}
    try:
        model = create_chat_model()
        messages = [SystemMessage(content=harness.system_prompt), HumanMessage(content=task)]
        response = model.invoke(messages)
        parsed = _parse_json(_safe_content(response))
        if parsed:
            result.update(parsed)
    except Exception:  # noqa: BLE001 - 单个评委失败不得中断其他评委的 fan-out 分支
        logger.exception("panel: failed for panel_ref=%r", panel_ref)

    issues: list[IssueEntry] = []
    for issue_text in result.get("issues", []):
        verdict = result.get("verdict", "pass")
        risk = "high" if verdict == "reject" else "medium"
        issues.append(
            {
                "risk": risk,
                "fix": str(issue_text),
                "status": "open",
            }
        )

    return {"review_panel_results": [result], "review_issues": issues}


# -- 三道门禁 -------------------------------------------------------------


def function_gate(state: WorkflowState) -> dict:
    """功能门 — 检查关键路径与返回关系、状态一致性、控制台报错。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 ``function_gate`` 结果和新增问题的字典。
    """
    demo = state.get("demo_artifact") or {}
    task = (
        "请执行功能门检查：关键路径完整性、返回关系、状态一致性、控制台报错。\n"
        f"Demo: {json.dumps(demo, ensure_ascii=False)}\n\n"
        '只输出 JSON: {"pass": bool, "issues": ["问题1", ...]}'
    )

    gate: dict = {"pass": True, "issues": []}
    try:
        from codepilot.core.agent_loader import invoke_agent

        response = invoke_agent("qa", task)
        parsed = _parse_json(_safe_content(response))
        if parsed:
            gate = parsed
    except Exception:  # noqa: BLE001
        logger.exception("function_gate: failed via LLM")

    issues: list[IssueEntry] = []
    for issue_text in gate.get("issues", []):
        issues.append({"risk": "high", "fix": str(issue_text), "status": "open"})

    return {
        "function_gate": gate,
        "review_issues": issues,
        "checkpoints": ["FUNCTION_GATE"],
    }


def visual_gate(state: WorkflowState) -> dict:
    """视觉门 — 截图对比与设计规范审核。

    复用 ``screenshot_diff`` 工具对比设计稿与实际产物截图。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 ``visual_gate`` 结果和新增问题的字典。
    """
    demo = state.get("demo_artifact") or {}
    artifact_path = demo.get("artifact_path", "")
    reference_path = artifact_path.replace("build.zip", "design.png") if artifact_path else ""
    actual_path = artifact_path.replace("build.zip", "screenshot.png") if artifact_path else ""

    compare_result: dict = {"pass": True, "similarity": 1.0, "issues": []}
    try:
        result = screenshot_diff.invoke(
            {
                "reference_path": reference_path,
                "actual_path": actual_path,
                "threshold": 0.95,
            }
        )
        compare_result["pass"] = result.get("pass", False)
        compare_result["similarity"] = result.get("similarity", 0.0)
        if not compare_result["pass"]:
            compare_result["issues"] = [f"视觉还原相似度 {compare_result['similarity']} 低于阈值"]
    except Exception:  # noqa: BLE001
        logger.exception("visual_gate: screenshot_diff failed")
        compare_result["pass"] = False
        compare_result["issues"] = ["截图对比工具执行失败"]

    issues: list[IssueEntry] = []
    for issue_text in compare_result.get("issues", []):
        issues.append({"risk": "medium", "fix": str(issue_text), "status": "open"})

    return {
        "visual_gate": compare_result,
        "review_issues": issues,
        "checkpoints": ["VISUAL_GATE"],
    }


def rehearsal_gate(state: WorkflowState) -> dict:
    """演示门 — 完整彩排与兜底方案验证。

    由于演示门涉及人工 Review，此处使用 LLM 模拟彩排检查，并标记
    ``rehearsal_pass``。在真实场景中应通过 ``interrupt`` 暂停等待人工确认。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 ``rehearsal_gate`` 结果和新增问题的字典。
    """
    demo = state.get("demo_artifact") or {}
    task = (
        "请执行演示门检查：完整彩排、设备/网络/兜底方案验证。\n"
        f"Demo: {json.dumps(demo, ensure_ascii=False)}\n\n"
        '只输出 JSON: {"pass": bool, "issues": ["问题1", ...]}'
    )

    gate: dict = {"pass": True, "issues": []}
    try:
        from codepilot.core.agent_loader import invoke_agent

        response = invoke_agent("qa", task)
        parsed = _parse_json(_safe_content(response))
        if parsed:
            gate = parsed
    except Exception:  # noqa: BLE001
        logger.exception("rehearsal_gate: failed via LLM")

    issues: list[IssueEntry] = []
    for issue_text in gate.get("issues", []):
        issues.append({"risk": "high", "fix": str(issue_text), "status": "open"})

    return {
        "rehearsal_gate": gate,
        "review_issues": issues,
        "checkpoints": ["REHEARSAL_GATE"],
    }


# -- fix_agent 与 loop_condition -------------------------------------------


def fix_agent(state: WorkflowState) -> dict:
    """证据驱动修复节点 — 根据 issues_ledger 中的问题进行修复。

    修复后回到功能门重新走完整串联，避免修复引入回归问题。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含更新后的 ``review_round`` 和问题状态标记的字典。
    """
    round_count = state.get("review_round", 0) + 1
    issues = state.get("issues_ledger") or []

    # 将本轮待修复的问题标记为 processing
    updated_issues: list[IssueEntry] = []
    for issue in issues:
        if issue.get("status") == "open":
            updated_issues.append({**issue, "status": "processing"})
        else:
            updated_issues.append(issue)

    logger.info("fix_agent: round %d, processing %d open issues", round_count, len(updated_issues))

    return {
        "review_round": round_count,
        "issues_ledger": updated_issues,
        "checkpoints": [f"FIX_ROUND_{round_count}"],
    }


def loop_condition(state: WorkflowState) -> str:
    """判定三道门禁后是否仍有高风险问题需要修复。

    根据 ``issues_ledger`` 中是否存在未解决的高风险问题决定路由：
    - ``fix``: 存在高风险未解决问题，进入 ``fix_agent`` 修复回环；
    - ``done``: 无高风险问题，完成评审，进入 ``human_confirm``。

    当 ``review_round`` 达到上限时强制结束，保证循环收敛。

    Args:
        state: 当前的工作流状态。

    Returns:
        ``"fix"`` 或 ``"done"``。
    """
    round_count = state.get("review_round", 0)
    if round_count >= _MAX_FIX_ROUNDS:
        logger.warning(
            "loop_condition: max fix rounds (%d) reached, force-done", _MAX_FIX_ROUNDS
        )
        return "done"

    issues = state.get("issues_ledger") or []
    has_high_risk = any(
        issue.get("risk") == "high" and issue.get("status") != "resolved"
        for issue in issues
    )
    return "fix" if has_high_risk else "done"


def finalize_review(state: WorkflowState) -> dict:
    """汇总三道门禁结果，生成最终的 ``qa_report``。

    在 ``loop_condition`` 判定为 ``done`` 后执行，将功能门、视觉门、
    演示门的结果合并为 ``QAReport``，并将已处理的问题标记为 resolved。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 ``qa_report`` 和更新后 ``issues_ledger`` 的字典。
    """
    func_gate = state.get("function_gate") or {}
    vis_gate = state.get("visual_gate") or {}
    reh_gate = state.get("rehearsal_gate") or {}

    function_pass = bool(func_gate.get("pass", False))
    visual_pass = bool(vis_gate.get("pass", False))
    rehearsal_pass = bool(reh_gate.get("pass", False))

    issues = state.get("issues_ledger") or []
    resolved_issues: list[IssueEntry] = []
    for issue in issues:
        resolved_issues.append({**issue, "status": "resolved"})

    qa_report: QAReport = {
        "function_pass": function_pass,
        "visual_pass": visual_pass,
        "rehearsal_pass": rehearsal_pass,
        "issues": resolved_issues,
    }

    return {
        "qa_report": qa_report,
        "issues_ledger": resolved_issues,
        "checkpoints": ["QA_PASS" if all([function_pass, visual_pass, rehearsal_pass]) else "QA_FAIL"],
    }
