"""对传入的任务进行分类，并确定下一个工作流步骤。"""

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from codepilot.core.create_model import create_chat_model
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_VALID_STEPS = ("research", "data", "produce", "qa")

_CLASSIFIER_SYSTEM_PROMPT = """\
你是多 Agent 工作流的调度中心（Orchestrator）。你的唯一职责是判断任务当前应该
从下列四个阶段的哪一个进入，四个阶段严格按顺序执行：research -> data -> \
produce -> qa。

判断依据：
- 如果目标（goal）缺乏事实依据、尚未研究过背景信息，或 facts_ledger 为空，选择 "research"。
- 如果已有研究结论（facts_ledger 非空）但还没有明确的方案/规格（spec 为空），选择 "data"。
- 如果已有 spec/evidence 但还没有 Demo 产物（demo_artifact 为空），选择 "produce"。
- 如果已有 demo_artifact 但还没有质检报告（qa_report 为空），选择 "qa"。

只输出一个 JSON 对象，不要输出任何其他文字，格式为：
{"next_step": "research" | "data" | "produce" | "qa", "reason": "简要理由"}
"""


def _build_classification_context(state: WorkflowState) -> str:
    return json.dumps(
        {
            "goal": state.get("goal", ""),
            "scope": state.get("scope", ""),
            "has_facts": bool(state.get("facts_ledger")),
            "has_spec": bool(state.get("spec")),
            "has_demo_artifact": bool(state.get("demo_artifact")),
            "has_qa_report": bool(state.get("qa_report")),
        },
        ensure_ascii=False,
    )


def _fallback_next_step(state: WorkflowState) -> str:
    """当 LLM 调用失败时使用的确定性兜底分类逻辑。"""
    if not state.get("facts_ledger"):
        return "research"
    if not state.get("spec"):
        return "data"
    if not state.get("demo_artifact"):
        return "produce"
    return "qa"


def classify_task(state: WorkflowState) -> dict:
    """对任务目标进行分类，并通过 LLM 推理设定下一步。

    如果 LLM 调用失败或返回无法解析的结果，则回退到基于哪些
    状态字段已经填充的确定性规则，以保证图不会因分类错误而卡住。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含更新后 next_step 的字典。
    """
    goal = state.get("goal", "")
    if not goal:
        return {"next_step": "research"}

    try:
        # 分类节点是轻量调用，缩短超时与重试避免拖累主流程
        model = create_chat_model(timeout=30, max_retries=1)
        messages = [
            SystemMessage(content=_CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=_build_classification_context(state)),
        ]
        response = model.invoke(messages)
        parsed = extract_json(safe_content(response))
        if isinstance(parsed, dict):
            next_step = parsed.get("next_step")
            if next_step in _VALID_STEPS:
                return {"next_step": next_step}
        logger.warning("classify_task: LLM returned invalid next_step, falling back")
    except Exception:  # noqa: BLE001 - 任何 LLM/解析失败都不应让图崩溃
        logger.exception("classify_task: LLM classification failed, falling back to rule-based logic")

    return {"next_step": _fallback_next_step(state)}
