"""主图闭环间 rerun：facts 变更重跑 Decision，spec 变更回归 Production。"""

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
    if prev and prev != fp:
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
    """QA 结束后决定是重跑上游还是进入人工确认。"""
    rerun = int(state.get("loop_rerun_count") or 0)
    if rerun >= _MAX_LOOP_RERUNS:
        logger.warning("route_after_qa: max loop reruns (%d) reached", _MAX_LOOP_RERUNS)
        return "human_confirm"

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
