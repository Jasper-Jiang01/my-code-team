"""事实台账 / 问题台账条目的构造辅助。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from codepilot.states.models import FactEntryModel, IssueEntryModel, RuleEntryModel
from codepilot.states.workflow_state import FactEntry, IssueEntry, RuleEntry


def _stable_id(*parts: str) -> str:
    raw = "|".join(part.strip() for part in parts if part)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def make_fact(
    *,
    source: str,
    metric: str,
    value: str = "",
    definition: str = "",
    url: str = "",
    snippet: str = "",
    timestamp: str | None = None,
) -> FactEntry:
    """构造一条带稳定 id 的事实。无证据正文时仍允许记录来源与口径。"""
    return FactEntryModel(
        id=_stable_id(source, metric, url or value),
        source=source,
        metric=metric,
        definition=definition,
        value=value,
        url=url,
        snippet=snippet or value,
        timestamp=timestamp or datetime.now(UTC).isoformat(),
    ).model_dump()


def make_rule(*, domain: str, content: str) -> RuleEntry:
    return RuleEntryModel(
        id=_stable_id("rule", domain, content),
        domain=domain,
        content=content,
    ).model_dump()


def make_issue(
    *,
    source: str,
    message: str,
    risk: str,
    evidence: str = "",
    status: str = "open",
) -> IssueEntry:
    """构造一条带稳定 id 的问题。相同 source+message 会 upsert 而不是复制。"""
    return IssueEntryModel(
        id=_stable_id(source, message),
        source=source,
        risk=risk,
        fix=message,
        evidence=evidence,
        status=status,
    ).model_dump()


def resolve_issues(issues: list[IssueEntry], *, source: str | None = None) -> list[IssueEntry]:
    """返回将匹配问题标记为 resolved 的 upsert 补丁（只含需要更新的条目）。"""
    updates: list[IssueEntry] = []
    for issue in issues:
        if issue.get("status") == "resolved":
            continue
        if source is not None and issue.get("source") != source:
            continue
        if not issue.get("id"):
            continue
        updates.append({**issue, "status": "resolved"})
    return updates
