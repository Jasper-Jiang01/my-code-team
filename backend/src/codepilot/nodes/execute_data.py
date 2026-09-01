"""数据 Agent 节点 — 执行指标测算与数据验证。"""

import json
import logging

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.states.workflow_state import RuleEntry, WorkflowState

logger = logging.getLogger(__name__)

_DATA_TASK_TEMPLATE = """\
目标（goal）：{goal}
范围（scope）：{scope}

研究阶段已收集的事实（facts_ledger）：
{facts}

请基于以上事实进行指标口径校验和规模测算，输出用于支撑后续方案设计的结论。
只输出 JSON，不要输出其他文字，格式为：
{{
  "metrics": [{{"name": "指标名", "value": "取值/口径说明"}}],
  "rules": [{{"domain": "product|design|dev", "content": "规则内容"}}],
  "data_quality": "high|medium|low"
}}
"""


def _parse_data_result(raw_content: str) -> dict:
    parsed = extract_json(raw_content)
    return parsed if isinstance(parsed, dict) else {}


def execute_data(state: WorkflowState) -> dict:
    """执行数据分析阶段。

    加载 ``data`` Agent Harness（``agents/data.yaml``），让其基于已收集的
    事实验证指标口径并产出规模估算，并将结果规则追加到
    ``rules_ledger``，供下游对抗式验证循环
    （``producer`` / ``critic`` / ``judge``）使用。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含更新后 ``rules_ledger`` 和完成 checkpoint 的字典。
    """
    goal = state.get("goal", "")
    facts = state.get("facts_ledger", [])

    rules: list[RuleEntry] = []
    if goal:
        try:
            task = _DATA_TASK_TEMPLATE.format(
                goal=goal,
                scope=state.get("scope", "未指定"),
                facts=json.dumps(facts, ensure_ascii=False) if facts else "（暂无）",
            )
            response = invoke_agent("data", task)
            result = _parse_data_result(safe_content(response))
            for rule in result.get("rules", []):
                if isinstance(rule, dict) and rule.get("domain") and rule.get("content"):
                    rules.append({"domain": str(rule["domain"]), "content": str(rule["content"])})
        except Exception:  # noqa: BLE001 - 不能因为数据分析失败而让图崩溃
            logger.exception("execute_data: failed to compute metrics via LLM")

    return {
        "rules_ledger": rules,
        "checkpoints": ["data_done"],
    }
