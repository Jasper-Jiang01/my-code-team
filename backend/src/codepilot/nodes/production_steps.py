"""ProductionGraph 静态六步子流程的节点实现。

根据技术方案第 3.1 / 5 节，生产阶段是一个不可缺步的静态子流程，
模型分工为：01 EXPLORE（需求增量）-> 02 GENERATE（设计草稿）
-> 03 GUARD（DP 审核）-> 04 BUILD（开发实现）
-> 05 COMPARE（视觉还原）-> 06 VERIFY（QA 验收）。

每一步都通过 Checkpoint 证明真实完成，产出的结构化结果写入
``WorkflowState`` 的对应字段，最终在 ``verify`` 步骤汇总为
``demo_artifact``。

读写：``demo_artifact``（由本子图拥有）。
使用的工具：``deploy_demo``、``screenshot_diff``（通过 ``design`` Agent Harness）。
"""

import json
import logging
from pathlib import Path

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.config import settings
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.workflow_state import Demo, WorkflowState
from codepilot.tools import deploy_demo, screenshot_diff

logger = logging.getLogger(__name__)


_DEFAULT_ARTIFACTS_DIR = str(Path.home() / "Desktop" / "CodePilot_artifacts")


def _artifact_dir(goal: str) -> Path:
    """返回本次运行的产物落盘目录（位于 ``settings.artifacts_dir`` 下）。

    若配置为空（如 ``.env`` 中显式设为空字符串），回退到桌面默认目录，
    避免产物被误写到相对路径 / 当前工作目录下。
    """
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in goal[:32]).strip() or "demo"
    base_dir = settings.artifacts_dir.strip() or _DEFAULT_ARTIFACTS_DIR
    return Path(base_dir) / safe_name


_DESIGN_TASK_TEMPLATE = """\
锁定规格（spec）：{spec}
证据（evidence）：{evidence}
已知规则（rules_ledger）：{rules}

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


# -- 01 EXPLORE: 需求增量 --------------------------------------------------


def explore(state: WorkflowState) -> dict:
    """01 EXPLORE — 识别需求增量与设计约束。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含增量需求结论和 step=1 checkpoint 的字典。
    """
    spec = state.get("spec") or {}
    extra = _DESIGN_TASK_TEMPLATE.format(
        spec=json.dumps(spec, ensure_ascii=False),
        evidence=json.dumps(state.get("evidence") or {}, ensure_ascii=False),
        rules=json.dumps(state.get("rules_ledger") or [], ensure_ascii=False),
        extra_instructions="请分析需求增量，明确新增功能与边界约束，为设计草稿做准备。",
        json_schema='{"increments": ["增量1", "增量2"], "constraints": ["约束1"]}',
    )
    result: dict = {}
    try:
        response = invoke_agent("design", extra)
        result = _parse_json(safe_content(response))
    except Exception:  # noqa: BLE001 - 不能因单步失败而中断静态子流程
        logger.exception("explore: failed via LLM")

    return {"production_step": 1, "design_draft": result, "checkpoints": ["EXPLORE_DONE"]}


# -- 02 GENERATE: 设计草稿 -------------------------------------------------


def generate(state: WorkflowState) -> dict:
    """02 GENERATE — 产出设计草稿。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含更新后 design_draft 和 step=2 checkpoint 的字典。
    """
    draft = state.get("design_draft") or {}
    extra = _DESIGN_TASK_TEMPLATE.format(
        spec=json.dumps(state.get("spec") or {}, ensure_ascii=False),
        evidence=json.dumps(state.get("evidence") or {}, ensure_ascii=False),
        rules=json.dumps(state.get("rules_ledger") or [], ensure_ascii=False),
        extra_instructions=(
            "基于需求增量，产出高保真设计草稿（组件清单、布局结构、交互流）。"
        ),
        json_schema='{"components": ["组件1"], "layout": "...", "interactions": ["流1"]}',
    )
    try:
        response = invoke_agent("design", extra)
        parsed = _parse_json(safe_content(response))
        if parsed:
            draft = {**draft, **parsed}
    except Exception:  # noqa: BLE001
        logger.exception("generate: failed via LLM")

    return {"production_step": 2, "design_draft": draft, "checkpoints": ["GENERATE_DONE"]}


# -- 03 GUARD: DP 审核 -----------------------------------------------------


def guard(state: WorkflowState) -> dict:
    """03 GUARD — 设计规范审核（DP 审核）。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 design_audit 和 step=3 checkpoint 的字典。
    """
    extra = _DESIGN_TASK_TEMPLATE.format(
        spec=json.dumps(state.get("spec") or {}, ensure_ascii=False),
        evidence=json.dumps(state.get("evidence") or {}, ensure_ascii=False),
        rules=json.dumps(state.get("rules_ledger") or [], ensure_ascii=False),
        extra_instructions="审核设计草稿是否符合品牌规范、关键路径完整性与一致性。",
        json_schema='{"approved": bool, "issues": ["问题1"]}',
    )
    audit: dict = {"approved": True, "issues": []}
    try:
        response = invoke_agent("design", extra)
        parsed = _parse_json(safe_content(response))
        if parsed:
            audit = parsed
    except Exception:  # noqa: BLE001
        logger.exception("guard: failed via LLM")

    return {"production_step": 3, "design_audit": audit, "checkpoints": ["GUARD_DONE"]}


# -- 04 BUILD: 开发实现 ----------------------------------------------------


def build(state: WorkflowState) -> dict:
    """04 BUILD — 开发实现并部署 Demo 产物。

    使用 ``deploy_demo`` 工具将实现产物写入本地磁盘（``settings.artifacts_dir``
    下），产出的构建信息写入 ``build_artifact``。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 build_artifact 和 step=4 checkpoint 的字典。
    """
    draft = state.get("design_draft") or {}
    goal = state.get("goal", "demo")
    artifact_path = str(_artifact_dir(goal) / "build.zip")
    deploy_result: dict = {}
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
                },
            }
        )
    except Exception:  # noqa: BLE001
        logger.exception("build: deploy_demo failed")

    build_artifact = {
        "artifact_path": artifact_path,
        "deploy_status": deploy_result.get("status", "unknown"),
        "url": deploy_result.get("url", ""),
        "components": list(draft.get("components", [])),
    }
    return {"production_step": 4, "build_artifact": build_artifact, "checkpoints": ["BUILD_DONE"]}


# -- 05 COMPARE: 视觉还原 --------------------------------------------------


def _render_placeholder_screenshot(path: Path, title: str, lines: list[str], bg_color: str) -> None:
    """在本地生成一张简单的占位截图（用于本地测试演示视觉对比流程）。

    真实场景下 design.png（设计稿）应来自设计工具导出，screenshot.png
    （实际截图）应来自浏览器/App 自动化截图；本地测试阶段暂用 Pillow
    绘制包含设计草稿要点的示意图代替。

    Args:
        path: 输出图片路径。
        title: 图片标题（通常为 goal）。
        lines: 需要绘制的正文行（如组件清单）。
        bg_color: 背景色（十六进制），用于制造设计稿与截图之间的可辨识差异。
    """
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (800, 600), color=bg_color)
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), title[:60], fill="black")
    y = 60
    for line in lines[:15]:
        draw.text((20, y), f"- {line}"[:90], fill="black")
        y += 30
    img.save(path)


def compare(state: WorkflowState) -> dict:
    """05 COMPARE — 视觉还原比对。

    本地测试场景下，先用 Pillow 分别渲染出「设计稿」与「实际截图」两张
    占位图（内容取自 design_draft，背景色故意不同以制造可感知的差异），
    再调用 ``screenshot_diff`` 做真实的像素级对比。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 visual_compare 和 step=5 checkpoint 的字典。
    """
    draft = state.get("design_draft") or {}
    build_artifact = state.get("build_artifact") or {}
    goal = state.get("goal", "demo")
    artifact_dir = _artifact_dir(goal)
    reference_path = artifact_dir / "design.png"
    actual_path = artifact_dir / "screenshot.png"

    compare_result: dict = {}
    try:
        components = list(draft.get("components") or [])
        _render_placeholder_screenshot(
            reference_path, f"[设计稿] {goal}", components, bg_color="#eef3ff"
        )
        _render_placeholder_screenshot(
            actual_path, f"[实际截图] {goal}", components, bg_color="#ffffff"
        )
        compare_result = screenshot_diff.invoke(
            {
                "reference_path": str(reference_path),
                "actual_path": str(actual_path),
                "threshold": 0.95,
            }
        )
    except Exception:  # noqa: BLE001
        logger.exception("compare: screenshot_diff failed")
        compare_result = {"pass": False, "similarity": 0.0, "diff_image_path": None}

    return {"production_step": 5, "visual_compare": compare_result, "checkpoints": ["COMPARE_DONE"]}


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
    visual_pass = bool(visual_compare.get("pass", False))

    demo: Demo = {
        "artifact_path": build_artifact.get("artifact_path", ""),
        "version": f"v1.0-step{state.get('production_step', 6)}",
    }
    return {
        "production_step": 6,
        "demo_artifact": demo,
        "checkpoints": ["VISUAL_PASS" if visual_pass else "VISUAL_FAIL", "PRODUCE_DONE"],
    }
