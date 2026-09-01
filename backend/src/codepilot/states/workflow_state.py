"""所有 Agent 和图共享的全局工作流状态。"""

from typing import Annotated, TypedDict

from codepilot.states.reducers import unique_extend, upsert_by_id


class FactEntry(TypedDict, total=False):
    """事实台账中的单条事实条目（来源 / 口径 / 取值 / 时间）。"""

    id: str
    source: str
    metric: str
    definition: str
    value: str
    url: str
    snippet: str
    timestamp: str


class RuleEntry(TypedDict, total=False):
    """规则台账中的单条规则条目。"""

    id: str
    domain: str
    content: str


class IssueEntry(TypedDict, total=False):
    """问题台账中的单条问题条目（风险 / 证据 / 修复 / 验收）。"""

    id: str
    source: str
    risk: str
    fix: str
    evidence: str
    status: str


class Spec(TypedDict):
    """工作流的锁定规格说明。"""

    goal: str
    scope: str
    constraints: list[str]


class Evidence(TypedDict):
    """在研究与数据阶段收集到的证据。"""

    facts: list[FactEntry]
    rules: list[RuleEntry]


class Demo(TypedDict, total=False):
    """在生产阶段产出的 Demo 产物。"""

    artifact_path: str
    version: str
    fix_notes: list[str]


class QAReport(TypedDict):
    """质量门禁执行后生成的 QA 报告。"""

    function_pass: bool
    visual_pass: bool
    rehearsal_pass: bool
    issues: list[IssueEntry]


class WorkflowState(TypedDict):
    """贯穿所有工作流阶段的全局状态总线。"""

    # 目标与约束
    goal: str
    scope: str
    constraints: Annotated[list[str], unique_extend]
    exit_conditions: Annotated[list[str], unique_extend]

    # 三大台账（按 id upsert，允许更新 status / 证据而不重复膨胀）
    facts_ledger: Annotated[list[FactEntry], upsert_by_id]
    rules_ledger: Annotated[list[RuleEntry], upsert_by_id]
    issues_ledger: Annotated[list[IssueEntry], upsert_by_id]

    # 阶段产物
    spec: Spec | None
    evidence: Evidence | None
    demo_artifact: Demo | None
    qa_report: QAReport | None

    # 控制信号
    checkpoints: Annotated[list[str], unique_extend]
    next_step: str
    human_confirm: bool | None

    # 内部工作字段（不属于核心 State Bus 契约的一部分，仅用于
    # 在单个子图运行内在节点之间传递数据）
    research_queries: list[str]
    research_findings: Annotated[list[FactEntry], upsert_by_id]

    # DecisionGraph 对抗式验证的工作字段
    decision_proposal: dict | None
    decision_critique: dict | None
    decision_verdict: str | None
    decision_round: int
    decision_candidates: Annotated[list[dict], upsert_by_id]
    decision_shortlist: list[dict]

    # 闭环间 rerun 指纹
    last_decided_facts_fp: str
    last_produced_spec_fp: str
    loop_rerun_count: int

    # ProductionGraph 静态六步子流程的工作字段
    production_step: int
    production_guard_round: int
    design_draft: dict | None
    design_audit: dict | None
    build_artifact: dict | None
    visual_compare: dict | None

    # ReviewGraph 对抗式评审的工作字段
    review_panel_results: Annotated[list[dict], upsert_by_id]
    review_issues: Annotated[list[IssueEntry], upsert_by_id]
    review_round: int
    function_gate: dict | None
    visual_gate: dict | None
    rehearsal_gate: dict | None
