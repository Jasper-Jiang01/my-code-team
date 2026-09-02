"""主图闭环间 rerun：QA 修复阶段显式回写 qa_reopen_target 触发重跑。

此前版本依赖 facts_ledger / spec 的指纹比较来决定是否重跑 Decision /
Production，但 QA 与修复阶段从不回写这两个字段，导致 rerun 分支永远不
触发，形成"看起来有闭环、实际不回归"的隐患。

现在 fix_agent 会根据问题根因显式写入 ``qa_reopen_target``：
- ``"data"``   -> 重跑 DecisionGraph（事实/数据层缺陷）
- ``"produce"`` -> 重跑 ProductionGraph（规格层缺陷）
- ``""``        -> 无需重跑，进入人工确认

指纹比较仍保留作为兜底信号，但主要触发路径改为显式回写。
"""

from __future__ import annotations

import logging

from codepilot.states.fingerprints import fingerprint_facts, fingerprint_spec
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_MAX_LOOP_RERUNS = 2


def mark_decision_snapshot(state: WorkflowState) -> dict:
    """DecisionGraph 结束后锁定本次消费的 facts 指纹。"""
    fp = fingerprint_facts(state.get("facts_ledger"))
    prev = state.get("last_decided_facts_fp") or ""
    rerun = int(state.get("loop_rerun_count") or 0)
    # 只有当之前已有快照且指纹确实变化时才计数 rerun
    if prev and fp and prev != fp:
        rerun += 1
    return {
        "last_decided_facts_fp": fp,
        "loop_rerun_count": rerun,
        "checkpoints": ["DECISION_SNAPSHOT"],
    }


def mark_production_snapshot(state: WorkflowState) -> dict:
    """ProductionGraph 结束后锁定本次消费的 spec 指纹。"""
    return {
        "last_produced_spec_fp": fingerprint_spec(state.get("spec")),
        "checkpoints": ["PRODUCTION_SNAPSHOT"],
    }


def route_after_qa(state: WorkflowState) -> str:
    """QA 结束后决定是重跑上游还是进入人工确认。

    优先读取 ``qa_reopen_target``（由 fix_agent 显式回写）；
    其次回退到指纹比较（兜底，QA 阶段通常不会回写 facts/spec，
    故指纹比较一般无法触发，仅在上游节点自行更新时生效）。
    """
    rerun = int(state.get("loop_rerun_count") or 0)
    if rerun >= _MAX_LOOP_RERUNS:
        logger.warning("route_after_qa: max loop reruns (%d) reached", _MAX_LOOP_RERUNS)
        return "human_confirm"

    # 1) 优先读取 QA 修复阶段显式回写的重跑目标
    reopen = (state.get("qa_reopen_target") or "").strip().lower()
    if reopen == "data":
        logger.info("route_after_qa: qa_reopen_target=data, rerun DecisionGraph")
        return "data"
    if reopen == "produce":
        logger.info("route_after_qa: qa_reopen_target=produce, rerun ProductionGraph")
        return "produce"

    # 2) 兜底：指纹比较（仅当上游节点在 QA 后自行更新了 facts/spec）
    facts_fp = fingerprint_facts(state.get("facts_ledger"))
    spec_fp = fingerprint_spec(state.get("spec"))
    decided = state.get("last_decided_facts_fp") or ""
    produced = state.get("last_produced_spec_fp") or ""

    if facts_fp and facts_fp != decided:
        logger.info("route_after_qa: facts_ledger changed, rerun DecisionGraph")
        return "data"
    if spec_fp and spec_fp != produced:
        logger.info("route_after_qa: spec changed, rerun ProductionGraph")
        return "produce"
    return "human_confirm"
