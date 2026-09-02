"""按用户需求匹配该走哪段流程、该调哪些工具。

调度中心以前只看台账空不空，新任务几乎总会 research → data → produce → qa，
于是口径查询会撞上 PDE，出原型图会先跑 SQL。这里用意图覆盖默认流水线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from codepilot.states.workflow_state import user_text

IntentKind = Literal[
    "knowledge",
    "research",
    "data",
    "spec",
    "prototype",
    "code",
    "review",
    "full",
]
GraphEntry = Literal["fast_qa", "research", "data", "produce", "qa"]

_KNOWLEDGE_TOOLS = ("search_km",)
_RESEARCH_TOOLS = ("search_km", "vector_memory")
_DATA_TOOLS = ("query_sql",)
_SPEC_TOOLS = ("pde_prototype",)
_PROTOTYPE_TOOLS = ("pde_prototype",)
_CODE_TOOLS = ("python_repl", "mcp_call", "deploy_demo")
_CODE_PAGE_TOOLS = ("pde_prototype", "python_repl", "mcp_call", "deploy_demo")
_REVIEW_TOOLS = ("screenshot_diff", "browser_screenshot", "deploy_demo")
_FULL_TOOLS = (
    "search_km",
    "vector_memory",
    "query_sql",
    "pde_prototype",
    "python_repl",
    "mcp_call",
    "deploy_demo",
    "screenshot_diff",
    "browser_screenshot",
)


@dataclass(frozen=True)
class TaskIntent:
    """一次需求对应的入口、工具白名单，以及 PDE 阶段。"""

    kind: IntentKind
    entry: GraphEntry
    tools: tuple[str, ...]
    pde_stage: str = ""


_PRESETS: dict[str, TaskIntent] = {
    "knowledge": TaskIntent("knowledge", "fast_qa", _KNOWLEDGE_TOOLS),
    "research": TaskIntent("research", "research", _RESEARCH_TOOLS),
    "data": TaskIntent("data", "data", _DATA_TOOLS),
    "spec": TaskIntent("spec", "produce", _SPEC_TOOLS, pde_stage="requirements"),
    "prototype": TaskIntent("prototype", "produce", _PROTOTYPE_TOOLS, pde_stage="design"),
    "code": TaskIntent("code", "produce", _CODE_TOOLS, pde_stage="code"),
    "review": TaskIntent("review", "qa", _REVIEW_TOOLS),
    "full": TaskIntent("full", "research", _FULL_TOOLS, pde_stage="design"),
}

# 生产类优先于问答启发式，避免「出个页面原型图」被短句规则打去 search_km。
_FULL_RE = re.compile(
    r"(做个|做一个|帮我做).{0,20}(demo|Demo)|全流程|端到端|可演示",
    re.IGNORECASE,
)
_PROTOTYPE_RE = re.compile(
    r"(原型图|页面原型|原型文件|html\s*原型|设计稿|高保真|"
    r"出个页面|出页面|出一份.{0,24}原型|出个.{0,16}原型|出原型|"
    r"画个原型|画原型)",
    re.IGNORECASE,
)
_SPEC_RE = re.compile(
    r"(需求文档|写需求|写一份需求|prd|PRD|规格说明|需求增量|需求规格)",
)
_CODE_RE = re.compile(
    r"(写代码|改代码|实现一个|开发一个|帮我实现|帮我写|代码开发|落地实现)",
    re.IGNORECASE,
)
_PAGE_RE = re.compile(r"(页面|原型|demo|Demo)")
_REVIEW_RE = re.compile(
    r"(质检|代码质量|视觉还原|评审|review)",
    re.IGNORECASE,
)
_DATA_RE = re.compile(r"(取数|规模测算|跑数|query_sql|\bSQL\b|\bsql\b)")
_RESEARCH_RE = re.compile(r"(调研|搜集资料|查资料|做研究)")
_SIMPLE_RE = re.compile(
    r"(什么|为什么|怎么|如何|是否|吗|呢|口径|是什么|介绍|解释|"
    r"区别|查询|查一下|看看|多少|哪个|哪些|谁|何时|"
    r"能不能|可以吗|意思)",
)


def preset(kind: str) -> TaskIntent:
    """按 kind 取预设；未知 kind 视为完整流水线。"""
    return _PRESETS.get(kind) or _PRESETS["full"]


def match_intent(goal: str) -> TaskIntent:
    """从用户原文判断意图。确定性规则，不调 LLM。"""
    text = (goal or "").strip()
    if not text:
        return _PRESETS["knowledge"]
    if _FULL_RE.search(text):
        return _PRESETS["full"]
    if _PROTOTYPE_RE.search(text):
        return _PRESETS["prototype"]
    if _SPEC_RE.search(text):
        return _PRESETS["spec"]
    if _CODE_RE.search(text):
        if _PAGE_RE.search(text):
            return TaskIntent("code", "produce", _CODE_PAGE_TOOLS, pde_stage="design")
        return _PRESETS["code"]
    if _REVIEW_RE.search(text):
        return _PRESETS["review"]
    if _DATA_RE.search(text):
        return _PRESETS["data"]
    if _SIMPLE_RE.search(text) or text.endswith(("?", "？")):
        return _PRESETS["knowledge"]
    if _RESEARCH_RE.search(text):
        return _PRESETS["research"]
    if len(text) <= 40:
        return _PRESETS["knowledge"]
    return _PRESETS["full"]


def resolve_intent(state: Mapping[str, object]) -> TaskIntent:
    """优先用本轮 triage 写入的意图，否则从 goal 重算。"""
    kind = str(state.get("task_intent") or "").strip()
    stored_tools = state.get("needed_tools")
    if kind:
        base = preset(kind)
        tools: tuple[str, ...] = base.tools
        if isinstance(stored_tools, Sequence) and not isinstance(stored_tools, (str, bytes)):
            names = tuple(str(item) for item in stored_tools if str(item).strip())
            if names:
                tools = names
        stage = str(state.get("pde_stage") or "").strip() or base.pde_stage
        return TaskIntent(base.kind, base.entry, tools, pde_stage=stage)
    return match_intent(user_text(state))


def needs_tool(state: Mapping[str, object], name: str) -> bool:
    """当前需求是否应该调用该工具。"""
    return name in resolve_intent(state).tools
