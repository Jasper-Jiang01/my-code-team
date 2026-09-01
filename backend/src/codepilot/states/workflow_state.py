"""Global workflow state shared across all agents and graphs."""

from typing import TypedDict, Optional, Annotated
from operator import add


class FactEntry(TypedDict):
    """A single fact entry in the facts ledger."""

    source: str
    metric: str
    timestamp: str


class RuleEntry(TypedDict):
    """A single rule entry in the rules ledger."""

    domain: str
    content: str


class IssueEntry(TypedDict):
    """A single issue entry in the issues ledger."""

    risk: str
    fix: str
    status: str


class Spec(TypedDict):
    """Locked specification for the workflow."""

    goal: str
    scope: str
    constraints: list[str]


class Evidence(TypedDict):
    """Evidence collected during research and data phases."""

    facts: list[FactEntry]
    rules: list[RuleEntry]


class Demo(TypedDict):
    """Demo artifact produced in the production phase."""

    artifact_path: str
    version: str


class QAReport(TypedDict):
    """QA report generated after quality gates."""

    function_pass: bool
    visual_pass: bool
    rehearsal_pass: bool
    issues: list[IssueEntry]


class WorkflowState(TypedDict):
    """Global state bus shared across all workflow stages."""

    # Goal & Constraints
    goal: str
    scope: str
    constraints: Annotated[list[str], add]
    exit_conditions: Annotated[list[str], add]

    # Three Ledgers
    facts_ledger: Annotated[list[FactEntry], add]
    rules_ledger: Annotated[list[RuleEntry], add]
    issues_ledger: Annotated[list[IssueEntry], add]

    # Stage Artifacts
    spec: Optional[Spec]
    evidence: Optional[Evidence]
    demo_artifact: Optional[Demo]
    qa_report: Optional[QAReport]

    # Control Signals
    checkpoints: Annotated[list[str], add]
    next_step: str
    human_confirm: Optional[bool]
