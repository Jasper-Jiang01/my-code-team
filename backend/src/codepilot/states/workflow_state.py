"""所有 Agent 和图共享的全局工作流状态。"""

from operator import add
from typing import Annotated, TypedDict


class FactEntry(TypedDict):
    """事实台账中的单条事实条目。"""

    source: str
    metric: str
    timestamp: str


class RuleEntry(TypedDict):
    """规则台账中的单条规则条目。"""

    domain: str
    content: str


class IssueEntry(TypedDict):
    """问题台账中的单条问题条目。"""

    risk: str
    fix: str
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


class Demo(TypedDict):
    """在生产阶段产出的 Demo 产物。"""

    artifact_path: str
    version: str


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
    constraints: Annotated[list[str], add]
    exit_conditions: Annotated[list[str], add]

    # 三大台账
    facts_ledger: Annotated[list[FactEntry], add]
    rules_ledger: Annotated[list[RuleEntry], add]
    issues_ledger: Annotated[list[IssueEntry], add]

    # 阶段产物
    spec: Spec | None
    evidence: Evidence | None
    demo_artifact: Demo | None
    qa_report: QAReport | None

    # 控制信号
    checkpoints: Annotated[list[str], add]
    next_step: str
    human_confirm: bool | None

    # 内部工作字段（不属于核心 State Bus 契约的一部分，仅用于
    # 在单个子图运行内在节点之间传递数据）
    research_queries: list[str]  # 由 execute_research 产出用于 fan-out 的种子查询
    research_findings: Annotated[list[FactEntry], add]  # 每个查询的发现，由 synthesize_results 合并

    # DecisionGraph 对抗式验证的工作字段
    decision_proposal: dict | None  # 由 `producer` 产出的最新提案
    decision_critique: dict | None  # 由 `critic` 产出的最新批评
    decision_verdict: str | None  # "pass" | "needs_fix"，由 `critic` 设置
    decision_round: int  # 到目前为止 producer -> critic 的轮数（循环保护）

    # ProductionGraph 静态六步子流程的工作字段
    production_step: int  # 当前静态子流程步号（1-6）
    design_draft: dict | None  # 02 设计草稿（GENERATE）
    design_audit: dict | None  # 03 DP 审核（GUARD）结果
    build_artifact: dict | None  # 04 开发实现（BUILD）产出
    visual_compare: dict | None  # 05 视觉还原（COMPARE）结果

    # ReviewGraph 对抗式评审的工作字段
    review_panel_results: Annotated[list[dict], add]  # 五岗位评委的独立评审结论
    review_issues: Annotated[list[IssueEntry], add]  # 评委发现的问题（合并到 issues_ledger）
    review_round: int  # fix_agent 修复轮数（循环保护）
    function_gate: dict | None  # 功能门检查结果
    visual_gate: dict | None  # 视觉门检查结果
    rehearsal_gate: dict | None  # 演示门检查结果
