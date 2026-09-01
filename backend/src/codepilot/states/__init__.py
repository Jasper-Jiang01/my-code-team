"""工作流系统的状态定义。"""

from codepilot.states.models import FactEntryModel, IssueEntryModel, RuleEntryModel
from codepilot.states.entries import make_fact, make_issue, make_rule, resolve_issues
from codepilot.states.reducers import unique_extend, upsert_by_id
from codepilot.states.workflow_state import (
    Demo,
    Evidence,
    FactEntry,
    IssueEntry,
    QAReport,
    RuleEntry,
    Spec,
    WorkflowState,
)

__all__ = [
    "Demo",
    "Evidence",
    "FactEntry",
    "FactEntryModel",
    "IssueEntryModel",
    "RuleEntryModel",
    "IssueEntry",
    "QAReport",
    "RuleEntry",
    "Spec",
    "WorkflowState",
    "make_fact",
    "make_issue",
    "make_rule",
    "resolve_issues",
    "unique_extend",
    "upsert_by_id",
]
