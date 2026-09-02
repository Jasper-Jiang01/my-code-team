"""主图闭环间 rerun：QA 修复阶段显式回写 qa_reopen_target 触发重跑。

此前版本依赖 facts_ledger / spec 的指纹比较来决定是否重跑 Decision /
Production，但 QA 与修复阶段从不回写这两个字段，导致 rerun 分支永远不
触发，形成"看起来有闭环、实际不回归"的隐患。

现在 fix_agent 会根据问题根因显式写入 ``qa_reopen_target``：
- ``"data"``   -> 重跑 DecisionGraph（事实/数据层缺陷）
- ``"produce"`` -> 重跑 ProductionGraph（规格层缺陷）
- ``""``        -> 无需重跑，进入人工确认

指纹比较仍保留作为兜底信号。``after_qa`` 用 ``Command`` 在跳转时
清空 ``qa_reopen_target`` 并统一递增 ``loop_rerun_count``，避免
produce 重跑绕过 ``mark_decision`` 时计数不涨，以及目标字段残留导致死循环。
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.types import Command

from codepilot.states.fingerprints import fingerprint_facts, fingerprint_spec
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_MAX_LOOP_RERUNS = 2


def mark_decision_snapshot(state: WorkflowState) -> dict:
    """DecisionGraph 结束后锁定本次消费的 facts 指纹。

    不再在此递增 ``loop_rerun_count``：主图重跑次数由 ``after_qa`` 统一计数，
    避免与显式 reopen 路径双重累加。
    """
    fp = fingerprint_facts(state.get("facts_ledger"))
    return {
        "last_decided_facts_fp": fp,
        "checkpoints": ["DECISION_SNAPSHOT"],
    }


def mark_production_snapshot(state: WorkflowState) -> dict:
    """ProductionGraph 结束后锁定本次消费的 spec 指纹。"""
    return {
        "last_produced_spec_fp": fingerprint_spec(state.get("spec")),
        "checkpoints": ["PRODUCTION_SNAPSHOT"],
    }


def route_after_qa(state: WorkflowState) -> str:
    """纯路由决策（可单测）：重跑上游或进入人工确认。

    优先读取 ``qa_reopen_target``；其次指纹比较。快照为空时不做指纹
    兜底，避免 classify 直达 ``qa`` 时误触发 rerun。
    """
    rerun = int(state.get("loop_rerun_count") or 0)
    if rerun >= _MAX_LOOP_RERUNS:
        logger.warning("route_after_qa: max loop reruns (%d) reached", _MAX_LOOP_RERUNS)
        return "human_confirm"

    reopen = (state.get("qa_reopen_target") or "").strip().lower()
    if reopen == "data":
        logger.info("route_after_qa: qa_reopen_target=data, rerun DecisionGraph")
        return "data"
    if reopen == "produce":
        logger.info("route_after_qa: qa_reopen_target=produce, rerun ProductionGraph")
        return "produce"

    facts_fp = fingerprint_facts(state.get("facts_ledger"))
    spec_fp = fingerprint_spec(state.get("spec"))
    decided = state.get("last_decided_facts_fp") or ""
    produced = state.get("last_produced_spec_fp") or ""

    # 快照为空说明尚未走过对应 mark_* 节点，跳过指纹分支
    if decided and facts_fp and facts_fp != decided:
        logger.info("route_after_qa: facts_ledger changed, rerun DecisionGraph")
        return "data"
    if produced and spec_fp and spec_fp != produced:
        logger.info("route_after_qa: spec changed, rerun ProductionGraph")
        return "produce"
    return "human_confirm"


def after_qa(
    state: WorkflowState,
) -> Command[Literal["data", "produce", "human_confirm"]]:
    """消费 QA 重跑目标：清空字段、统一计数，并用 Command 跳转。"""
    target = route_after_qa(state)
    rerun = int(state.get("loop_rerun_count") or 0)
    if target in {"data", "produce"}:
        return Command(
            update={
                "qa_reopen_target": "",
                "loop_rerun_count": rerun + 1,
            },
            goto=target,
        )
    return Command(
        update={"qa_reopen_target": ""},
        goto="human_confirm",
    )
