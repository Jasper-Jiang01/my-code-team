"""研究 Agent 节点 — 收集证据并构建事实台账。"""

import json
import logging

from codepilot.core.agent_loader import invoke_agent
from codepilot.core.memory_store import load_project_memory
from codepilot.states.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

_MAX_SEED_QUERIES = 5

_RESEARCH_TASK_TEMPLATE = """\
目标（goal）：{goal}
范围（scope）：{scope}

历史项目记忆中的已知关键词（可参考，不必全部使用）：{known_keywords}

请基于以上目标，产出用于内外部研究的种子查询列表（seed queries），每条查询
应聚焦一个具体、可检索的子问题。只输出 JSON，不要输出其他文字，格式为：
{{"seed_queries": ["查询1", "查询2", ...]}}
最多输出 {max_queries} 条。
"""


def _parse_seed_queries(raw_content: str) -> list[str]:
    try:
        parsed = json.loads(raw_content)
        queries = parsed.get("seed_queries", [])
        if isinstance(queries, list):
            return [str(q) for q in queries if str(q).strip()][:_MAX_SEED_QUERIES]
    except (json.JSONDecodeError, AttributeError):
        logger.warning("execute_research: failed to parse seed_queries from LLM output")
    return []


def execute_research(state: WorkflowState) -> dict:
    """执行研究阶段：推导用于 fan-out 的种子查询。

    加载 ``research`` Agent Harness（``agents/research.yaml``），让其将
    目标拆解为种子查询，并存入 ``research_queries``，以便 ``fan_out``
    步骤可以通过 ``Send`` 为每个查询分发一个 ``researcher`` 任务。

    Args:
        state: 当前的工作流状态。

    Returns:
        包含 ``research_queries`` 和更新后 checkpoint 标记的字典。
    """
    goal = state.get("goal", "")
    scope = state.get("scope", "")

    seed_queries: list[str] = []
    if goal:
        try:
            project_memory = load_project_memory()
            task = _RESEARCH_TASK_TEMPLATE.format(
                goal=goal,
                scope=scope or "未指定",
                known_keywords=project_memory.get("keywords", []),
                max_queries=_MAX_SEED_QUERIES,
            )
            response = invoke_agent("research", task)
            content = response.content if isinstance(response.content, str) else str(response.content)
            seed_queries = _parse_seed_queries(content)
        except Exception:  # noqa: BLE001 - 不能因为研究规划失败而让图崩溃
            logger.exception("execute_research: failed to derive seed queries via LLM")

    if not seed_queries and goal:
        # 确定性兼底：直接将目标本身作为单一查询进行研究。
        seed_queries = [goal]

    return {
        "research_queries": seed_queries,
        "checkpoints": ["research_planned"],
    }
