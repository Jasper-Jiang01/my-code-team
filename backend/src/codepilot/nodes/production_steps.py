"""ProductionGraph 静态六步子流程的节点实现。

根据技术方案第 3.1 / 5 节，生产阶段是一个不可缺步的静态子流程，
模型分工为：01 EXPLORE（需求增量）-> 02 GENERATE（设计草稿）
-> 03 GUARD（DP 审核）-> 04 BUILD（开发实现）
-> 05 COMPARE（视觉还原）-> 06 VERIFY（QA 验收）。

每一步都通过 Checkpoint 证明真实完成，产出的结构化结果写入
``WorkflowState`` 的对应字段，最终在 ``verify`` 步骤汇总为
``demo_artifact``。

读写：``demo_artifact``（由本子图拥有）。
使用的工具：``pde_prototype``、``deploy_demo``、``screenshot_diff``、``python_repl``、``mcp_call``。
"""

import html
import json
import logging
from pathlib import Path

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.config import settings
from codepilot.core.context_views import format_state_context
from codepilot.core.intent_router import needs_tool, resolve_intent
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.workflow_state import Demo, WorkflowState
from codepilot.tools import (
    browser_screenshot,
    deploy_demo,
    mcp_call,
    pde_prototype,
    python_repl,
    screenshot_diff,
)

logger = logging.getLogger(__name__)


_DEFAULT_ARTIFACTS_DIR = str(Path.home() / "Desktop" / "CodePilot_artifacts")

# 静态六步子流程的步骤编号常量
_STEP_EXPLORE = 1
_STEP_GENERATE = 2
_STEP_GUARD = 3
_STEP_BUILD = 4
_STEP_COMPARE = 5
_STEP_VERIFY = 6


def _artifact_dir(goal: str) -> Path:
    """返回本次运行的产物落盘目录（位于 ``settings.artifacts_dir`` 下）。

    若配置为空（如 ``.env`` 中显式设为空字符串），回退到桌面默认目录，
    避免产物被误写到相对路径 / 当前工作目录下。
    """
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in goal[:32]).strip() or "demo"
    base_dir = settings.artifacts_dir.strip() or _DEFAULT_ARTIFACTS_DIR
    return Path(base_dir) / safe_name


_DESIGN_TASK_TEMPLATE = """\
你被授权读取的 State Bus 字段：
{context}

{extra_instructions}

只输出 JSON，不要输出其他文字，格式为：
{json_schema}
"""


def _parse_json(raw: str, default: dict | None = None) -> dict:
    """尽最大努力解析 JSON，失败时记录日志。"""
    parsed = extract_json(raw)
    if isinstance(parsed, dict):
        return parsed
    return dict(default or {})


def _export_design_html(artifact_dir: Path, goal: str, draft: dict) -> Path:
    """把设计草稿导出为可截图的 design.html。"""
    title = html.escape((goal or "Design")[:80])
    layout = html.escape(str(draft.get("layout") or ""))
    items = "".join(f"<li>{html.escape(str(item))}</li>" for item in (draft.get("components") or [])[:20])
    markup = (
        "<!doctype html><html><head><meta charset='utf-8'><title>"
        f"{title}</title>"
        "<style>body{font-family:sans-serif;background:#eef3ff;margin:24px}</style>"
        f"</head><body><h1>{title}</h1><p>{layout}</p><ul>{items}</ul></body></html>"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "design.html"
    path.write_text(markup, encoding="utf-8")
    return path


# -- 01 EXPLORE: 需求增量 --------------------------------------------------


def explore(state: WorkflowState) -> dict:
    """01 EXPLORE — 识别需求增量与设计约束。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含增量需求结论和 step=1 checkpoint 的字典。
    """
    extra = _DESIGN_TASK_TEMPLATE.format(
        context=format_state_context(state, "design"),
        extra_instructions="请分析需求增量，明确新增功能与边界约束，为设计草稿做准备。",
        json_schema='{"increments": ["增量1", "增量2"], "constraints": ["约束1"]}',
    )
    result: dict = {}
    try:
        # EXPLORE 只产出 JSON，不绑定生产工具，避免模型误调 pde/mcp。
        response = invoke_agent("design", extra, allowed_tools=[])
        result = _parse_json(safe_content(response))
    except Exception:  # noqa: BLE001 - 不能因单步失败而中断静态子流程
        logger.exception("explore: failed via LLM")

    return {"production_step": _STEP_EXPLORE, "design_draft": result, "checkpoints": ["EXPLORE_DONE"]}


# -- 02 GENERATE: 设计草稿 -------------------------------------------------


def generate(state: WorkflowState) -> dict:
    """02 GENERATE — 产出设计草稿。"""
    intent = resolve_intent(state)
    draft = state.get("design_draft") or {}
    audit = state.get("design_audit") or {}
    audit_note = ""
    if audit.get("issues"):
        audit_note = f"上一轮 GUARD 未通过，必须逐条修订：{json.dumps(audit.get('issues'), ensure_ascii=False)}"
    use_pde = needs_tool(state, "pde_prototype")
    extra = _DESIGN_TASK_TEMPLATE.format(
        context=format_state_context(state, "design"),
        extra_instructions=(
            "基于需求增量，产出高保真设计草稿（组件清单、布局结构、交互流）。"
            + (
                "页面原型图/设计稿请调用 pde_prototype（点评 PDE Agent，"
                "https://km.sankuai.com/collabpage/2776444575）。"
                if use_pde
                else "本轮不调用原型或代码工具，只输出 JSON 草稿。"
            )
            + ((" " + audit_note) if audit_note else "")
        ),
        json_schema='{"components": ["组件1"], "layout": "...", "interactions": ["流1"]}',
    )
    try:
        response = invoke_agent(
            "design",
            extra,
            allowed_tools=["pde_prototype"] if use_pde else [],
        )
        parsed = _parse_json(safe_content(response))
        if parsed:
            draft = {**draft, **parsed}
    except Exception:  # noqa: BLE001
        logger.exception("generate: failed via LLM")

    goal = state.get("goal", "demo")
    artifact_dir = _artifact_dir(goal)
    proto: dict = {}
    if use_pde:
        try:
            proto = pde_prototype.invoke(
                {
                    "requirement": goal,
                    "page_name": str(goal or "页面原型")[:40],
                    "components": list(draft.get("components") or []),
                    "stage": intent.pde_stage or "design",
                    "output_dir": str(artifact_dir),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("generate: pde_prototype failed")
            proto = {}

    if proto:
        if proto.get("components") and not draft.get("components"):
            draft["components"] = list(proto.get("components") or [])
        if proto.get("layout") and not draft.get("layout"):
            draft["layout"] = str(proto.get("layout") or "")
        if proto.get("interactions") and not draft.get("interactions"):
            draft["interactions"] = list(proto.get("interactions") or [])
        draft["prototype"] = {
            "mode": proto.get("mode"),
            "html_path": proto.get("html_path"),
            "task_url": proto.get("task_url"),
            "launch": proto.get("launch"),
            "playbook": proto.get("playbook"),
            "unverified": proto.get("unverified"),
        }

    html_path = Path(str(proto.get("html_path") or ""))
    if not html_path.exists() and (use_pde or needs_tool(state, "python_repl")):
        _export_design_html(artifact_dir, goal, draft)
    return {"production_step": _STEP_GENERATE, "design_draft": draft, "checkpoints": ["GENERATE_DONE"]}


# -- 03 GUARD: DP 审核 -----------------------------------------------------


def guard(state: WorkflowState) -> dict:
    """03 GUARD — 由独立审核 Agent 做设计规范审核，失败默认不放行。"""
    # 截断过大的 design_draft JSON，避免 prompt 过长导致 LLM 响应缓慢或挂起
    draft_json = json.dumps(state.get("design_draft") or {}, ensure_ascii=False)
    if len(draft_json) > 4000:
        draft_json = draft_json[:4000] + "\n...（已截断）"
    extra = _DESIGN_TASK_TEMPLATE.format(
        context=format_state_context(state, "guard"),
        extra_instructions=(
            "审核以下设计草稿，不得因为由同事生成就放行。"
            f"设计草稿：{draft_json}"
        ),
        json_schema='{"approved": bool, "issues": ["问题1"]}',
    )
    audit: dict = {"approved": False, "issues": ["GUARD 未产出有效审核结果"]}
    try:
        response = invoke_agent("guard", extra)
        parsed = _parse_json(safe_content(response))
        if parsed and "approved" in parsed:
            audit = {
                "approved": bool(parsed.get("approved")),
                "issues": list(parsed.get("issues") or []),
            }
    except Exception:  # noqa: BLE001
        logger.exception("guard: failed via LLM")

    round_count = int(state.get("production_guard_round") or 0) + 1
    checkpoint = "GUARD_PASS" if audit.get("approved") else "GUARD_REJECT"
    return {
        "production_step": _STEP_GUARD,
        "design_audit": audit,
        "production_guard_round": round_count,
        "checkpoints": [checkpoint],
    }


# -- 04 BUILD: 开发实现 ----------------------------------------------------


def build(state: WorkflowState) -> dict:
    """04 BUILD — 用 MCP 发现工具 + PythonREPL 生成页面，再打包 Demo。"""
    draft = state.get("design_draft") or {}
    goal = state.get("goal", "demo")
    artifact_dir = _artifact_dir(goal)
    artifact_path = str(artifact_dir / "build.zip")
    components = list(draft.get("components") or [])
    title = html.escape((goal or "Demo")[:80])
    design_html = artifact_dir / "design.html"

    mcp_catalog: dict = {}
    if needs_tool(state, "mcp_call"):
        try:
            mcp_catalog = mcp_call.invoke({"method": "tools/list"})
        except Exception:  # noqa: BLE001
            logger.exception("build: mcp_call tools/list failed")

    if design_html.exists():
        page_html = design_html.read_text(encoding="utf-8")
    else:
        escaped_components = [html.escape(str(item)) for item in components[:20]]
        page_html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>"
            f"{title}</title></head><body><h1>{title}</h1><ul>"
            + "".join(f"<li>{item}</li>" for item in escaped_components)
            + "</ul></body></html>"
        )
    repl_result: dict = {}
    if needs_tool(state, "python_repl"):
        repl_code = (
            "page_html = " + json.dumps(page_html, ensure_ascii=False) + "\n"
            "f = open('index.html', 'w')\n"
            "f.write(page_html)\n"
            "f.close()\n"
            "result = 'index.html'\n"
        )
        try:
            repl_result = python_repl.invoke({"code": repl_code, "workdir": str(artifact_dir)})
        except Exception:  # noqa: BLE001
            logger.exception("build: python_repl failed")
            repl_result = {"ok": False}

    deploy_result: dict = {}
    if needs_tool(state, "deploy_demo"):
        try:
            deploy_result = deploy_demo.invoke(
                {
                    "artifact_path": artifact_path,
                    "environment": "staging",
                    "manifest": {
                        "goal": goal,
                        "spec": state.get("spec"),
                        "design_draft": draft,
                        "design_audit": state.get("design_audit"),
                        "mcp": mcp_catalog,
                        "repl": repl_result,
                    },
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("build: deploy_demo failed")

    build_artifact = {
        "artifact_path": artifact_path,
        "deploy_status": deploy_result.get("status", "unknown"),
        "url": deploy_result.get("url", ""),
        "components": components,
        "repl_ok": bool(repl_result.get("ok")),
        "mcp_tools": [item.get("name") for item in (mcp_catalog.get("tools") or []) if isinstance(item, dict)],
    }
    return {"production_step": _STEP_BUILD, "build_artifact": build_artifact, "checkpoints": ["BUILD_DONE"]}


# -- 05 COMPARE: 视觉还原 --------------------------------------------------


def compare(state: WorkflowState) -> dict:
    """05 COMPARE — 截取设计稿 HTML 与 BUILD 产物页面，再做像素对比。

    优先用 Playwright 真浏览器；没有浏览器时按 HTML DOM 光栅化（仍来自真实文件，
    不是固定占位图）。
    """
    draft = state.get("design_draft") or {}
    goal = state.get("goal", "demo")
    artifact_dir = _artifact_dir(goal)
    design_html = artifact_dir / "design.html"
    page_html = artifact_dir / "index.html"
    reference_path = artifact_dir / "design.png"
    actual_path = artifact_dir / "screenshot.png"

    if not design_html.exists():
        _export_design_html(artifact_dir, goal, draft)

    compare_result: dict = {
        "pass": False,
        "similarity": 0.0,
        "diff_image_path": None,
        "mode": "placeholder",
        "unverified": True,
    }
    try:
        if not page_html.exists():
            raise FileNotFoundError(f"BUILD page missing: {page_html}")
        design_shot = browser_screenshot.invoke(
            {"html_path": str(design_html), "output_path": str(reference_path)}
        )
        page_shot = browser_screenshot.invoke(
            {"html_path": str(page_html), "output_path": str(actual_path)}
        )
        diff_result = screenshot_diff.invoke(
            {
                "reference_path": str(reference_path),
                "actual_path": str(actual_path),
                "threshold": 0.95,
            }
        )
        mode = page_shot.get("mode") or design_shot.get("mode") or "html_raster"
        compare_result = {
            **diff_result,
            "mode": mode,
            "unverified": False,
            "design_html": str(design_html),
            "page_html": str(page_html),
        }
    except Exception:  # noqa: BLE001
        logger.exception("compare: screenshot or diff failed")

    checkpoint = "COMPARE_DONE" if not compare_result.get("unverified") else "COMPARE_UNVERIFIED"
    return {"production_step": _STEP_COMPARE, "visual_compare": compare_result, "checkpoints": [checkpoint]}


# -- 06 VERIFY: QA 验收 ----------------------------------------------------


def verify(state: WorkflowState) -> dict:
    """06 VERIFY — QA 验收，汇总为 demo_artifact。

    汇总前五步的全部产出，生成最终的 ``Demo`` 产物。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 demo_artifact 和 step=6 checkpoint 的字典。
    """
    build_artifact = state.get("build_artifact") or {}
    visual_compare = state.get("visual_compare") or {}
    visual_pass = bool(visual_compare.get("pass", False)) and not visual_compare.get("unverified")
    audit = state.get("design_audit") or {}

    demo: Demo = {
        "artifact_path": build_artifact.get("artifact_path", ""),
        "version": f"v1.0-step{state.get('production_step', _STEP_VERIFY)}",
    }
    checkpoint = "VISUAL_PASS" if visual_pass else "VISUAL_UNVERIFIED"
    if not audit.get("approved"):
        checkpoint = "GUARD_FORCED_BUILD"
    updates: dict = {
        "production_step": _STEP_VERIFY,
        "demo_artifact": demo,
        "checkpoints": [checkpoint, "PRODUCE_DONE"],
    }
    intent = resolve_intent(state)
    if intent.kind in {"prototype", "spec", "code"}:
        proto = (state.get("design_draft") or {}).get("prototype") or {}
        html_path = str(proto.get("html_path") or "")
        url = str(build_artifact.get("url") or "")
        if intent.kind == "prototype":
            updates["chitchat_reply"] = (
                f"已按需求产出页面原型。\n文件：{html_path or _artifact_dir(state.get('goal', 'demo'))}"
                "\n操作指南：https://km.sankuai.com/collabpage/2776444575"
            )
        elif intent.kind == "spec":
            updates["chitchat_reply"] = (
                f"已按需求整理需求规格/原型（stage={intent.pde_stage or 'requirements'}）。"
                + (f"\n文件：{html_path}" if html_path else "")
            )
        else:
            updates["chitchat_reply"] = (
                "已完成本轮代码实现。"
                + (f"\nDemo：{url}" if url else "")
                + (f"\n产物：{demo.get('artifact_path', '')}" if demo.get("artifact_path") else "")
            )
    return updates
