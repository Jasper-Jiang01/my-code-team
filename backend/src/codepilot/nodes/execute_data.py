"""数据 Agent 节点 — 执行指标测算与数据验证。"""

import json
import logging

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.context_views import format_state_context
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.core.memory_store import update_agent_memory, update_project_memory
from codepilot.states.entries import make_fact, make_rule
from codepilot.states.workflow_state import FactEntry, RuleEntry, WorkflowState
from codepilot.tools import query_sql

logger = logging.getLogger(__name__)

_DATA_TASK_TEMPLATE = """\
你被授权读取的 State Bus 字段：
{context}

工具已执行的取数结果（query_sql）：
{sql_rows}

你必须先调用 query_sql 工具补充或校验口径（只读 SELECT），再给出结论。
只输出 JSON，不要输出其他文字，格式为：
{{
  "metrics": [{{"name": "指标名", "value": "取值", "definition": "口径说明"}}],
  "rules": [{{"domain": "product|design|dev", "content": "规则内容"}}],
  "data_quality": "high|medium|low"
}}
"""


def _parse_data_result(raw_content: str) -> dict:
    parsed = extract_json(raw_content)
    return parsed if isinstance(parsed, dict) else {}


def _rows_to_facts(rows: list[dict], goal: str) -> list[FactEntry]:
    facts: list[FactEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric = str(row.get("metric") or row.get("name") or "sql_metric")
        value = str(row.get("value") if row.get("value") is not None else json.dumps(row, ensure_ascii=False))
        facts.append(
            make_fact(
                source="query_sql",
                metric=metric,
                definition=str(row.get("definition") or f"由 query_sql 为「{goal}」取数"),
                value=value,
                snippet=json.dumps(row, ensure_ascii=False),
            )
        )
    return facts


def execute_data(state: WorkflowState) -> dict:
    """执行数据分析阶段：先取数，再让 data Agent 解释口径并产出规则。"""
    goal = state.get("goal", "")
    sql = (
        "SELECT metric, value, definition FROM metrics "
        f"WHERE context = '{goal.replace(chr(39), '')[:80]}'"
    )

    sql_rows: list[dict] = []
    try:
        raw_rows = query_sql.invoke({"sql": sql})
        sql_rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    except Exception:  # noqa: BLE001
        logger.exception("execute_data: query_sql failed")

    metric_facts = _rows_to_facts(sql_rows, goal)
    rules: list[RuleEntry] = []
    parsed_metrics: list[dict] = []

    if goal:
        try:
            task = _DATA_TASK_TEMPLATE.format(
                context=format_state_context(state, "data"),
                sql_rows=json.dumps(sql_rows, ensure_ascii=False) if sql_rows else "（暂无）",
            )
            response = invoke_agent("data", task)
            result = _parse_data_result(safe_content(response))
            for rule in result.get("rules", []):
                if isinstance(rule, dict) and rule.get("domain") and rule.get("content"):
                    rules.append(make_rule(domain=str(rule["domain"]), content=str(rule["content"])))
            for metric in result.get("metrics", []):
                if not isinstance(metric, dict):
                    continue
                name = str(metric.get("name") or "")
                if not name:
                    continue
                parsed_metrics.append(metric)
                metric_facts.append(
                    make_fact(
                        source="data_agent",
                        metric=name,
                        definition=str(metric.get("definition") or ""),
                        value=str(metric.get("value") or ""),
                    )
                )
        except Exception:  # noqa: BLE001
            logger.exception("execute_data: failed to compute metrics via LLM")

    try:
        update_project_memory(data_metrics=parsed_metrics or sql_rows)
        update_agent_memory("data_agent", last_metric_count=len(metric_facts))
    except Exception:  # noqa: BLE001
        logger.exception("execute_data: failed to persist memory")

    return {
        "facts_ledger": metric_facts,
        "rules_ledger": rules,
        "checkpoints": ["data_done"],
    }
