"""生成过滤与锦标赛：并行产出候选方案，筛选后两两比较选出最优。"""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from langgraph.types import Send

from codepilot.nodes.producer import draft_proposal
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_CANDIDATE_COUNT = 3
_VARIANT_HINTS = (
    "偏保守：最小可行范围，强调风险、依赖与约束，不要扩 scope。",
    "偏增长：在事实允许的范围内扩大规模与指标，写清测算假设。",
    "偏体验：强调关键路径、设计规范与可演示性。",
)


class CandidateInput(TypedDict, total=False):
    goal: str
    scope: str
    facts_ledger: list
    rules_ledger: list
    constraints: list
    decision_critique: dict | None
    candidate_variant: int


def fan_out_candidates(state: WorkflowState) -> list[Send]:
    """为每个变体分发一个 candidate_producer 任务。"""
    base = {
        "goal": state.get("goal", ""),
        "scope": state.get("scope", ""),
        "facts_ledger": state.get("facts_ledger") or [],
        "rules_ledger": state.get("rules_ledger") or [],
        "constraints": list(state.get("constraints") or []),
        "decision_critique": state.get("decision_critique"),
    }
    return [
        Send("candidate_producer", {**base, "candidate_variant": index})
        for index in range(_CANDIDATE_COUNT)
    ]


def candidate_producer(payload: CandidateInput) -> dict:
    """单个变体的方案生成（Generate 阶段）。"""
    variant = int(payload.get("candidate_variant") or 0)
    hint = _VARIANT_HINTS[variant % len(_VARIANT_HINTS)]
    proposal = draft_proposal(payload, variant_hint=hint)
    proposal["id"] = f"candidate-{variant}"
    proposal["variant"] = variant
    return {"decision_candidates": [proposal]}


def _is_valid_candidate(item: Any) -> bool:
    return isinstance(item, dict) and bool(item.get("goal"))


def filter_candidates(state: WorkflowState) -> dict:
    """生成过滤：丢掉缺 goal/scope 的候选，保留可比较的提案。"""
    raw = [item for item in (state.get("decision_candidates") or []) if _is_valid_candidate(item)]
    filtered = []
    for item in raw:
        scope = str(item.get("scope") or "").strip()
        constraints = item.get("constraints") or []
        if not scope and not constraints:
            continue
        filtered.append(item)
    if not filtered:
        fallback = draft_proposal(state)
        fallback["id"] = "candidate-fallback"
        fallback["variant"] = -1
        filtered = [fallback]
        logger.warning("filter_candidates: no valid candidates, using fallback proposal")
    return {
        "decision_shortlist": filtered,
        "checkpoints": ["CANDIDATES_FILTERED"],
    }


def _score_proposal(proposal: dict, facts: list, rules: list) -> float:
    score = 0.0
    constraints = proposal.get("constraints") or []
    if isinstance(constraints, list):
        score += min(len(constraints), 5) * 2
    if str(proposal.get("scope") or "").strip():
        score += 3
    blob = json.dumps(proposal, ensure_ascii=False)
    for fact in facts:
        metric = str(fact.get("metric") or "")
        if metric and metric[:12] in blob:
            score += 1
            break
    for rule in rules:
        content = str(rule.get("content") or "")
        if content and content[:12] in blob:
            score += 1
            break
    if proposal.get("variant") == 0:
        score += 0.1  # 并列时偏保守
    return score


def _pairwise_winner(left: dict, right: dict, facts: list, rules: list) -> dict:
    left_score = _score_proposal(left, facts, rules)
    right_score = _score_proposal(right, facts, rules)
    return left if left_score >= right_score else right


def tournament(state: WorkflowState) -> dict:
    """锦标赛：对过滤后的候选做线性两两比较，胜者进入 critic。"""
    candidates = [
        item for item in (state.get("decision_shortlist") or state.get("decision_candidates") or [])
        if _is_valid_candidate(item)
    ]
    if not candidates:
        winner = draft_proposal(state)
        winner["id"] = "candidate-fallback"
    else:
        winner = candidates[0]
        facts = state.get("facts_ledger") or []
        rules = state.get("rules_ledger") or []
        for challenger in candidates[1:]:
            winner = _pairwise_winner(winner, challenger, facts, rules)

    logger.info("tournament: winner=%s variant=%s", winner.get("id"), winner.get("variant"))
    return {
        "decision_proposal": winner,
        "checkpoints": ["TOURNAMENT_WINNER"],
    }
