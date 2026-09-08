"""主工作流：``START → prelude → intent_route → {deep_agent | data_analysis} → epilogue → END``。

意图路由（不锁死路径）：prelude 之后由 ``intent_route`` 节点根据
用户意图分流——``data_analysis``（数据分析问题）进入数据分析子图
（``sub_graph_data``，含 4 个本地 pandas 工具 + ba_agent_analysis
兜底），其余（``general``）进入通用 DeepAgent。两条路径最终都
汇入 epilogue 提取最终答复。

意图判定策略（``IntentRouter``）：
1. 关键词快速通道：消息中明确提到数据文件（.csv/.xlsx/.json 等）
   时直接判定为 data_analysis，无需 LLM 调用；
2. LLM 结构化分类：其余消息交给 LLM 二分类（data_analysis / general）；
3. 失败兜底：LLM 调用异常或输出不可解析时回退 general——
   宁可走通用助手也不能因路由失败而卡死。

``deep_agent`` 是官方 DeepAgents 子图，**直接挂载**为主图节点（共享
``messages`` 通道）而不是在节点函数里手动 ``invoke``：这样子图内
``HumanInTheLoopMiddleware`` 触发的 interrupt 会自动冒泡到主图，
由主图 checkpointer 持久化，恢复时以 ``Command(resume={"decisions": ...})``
重新进入（见 ``api/app.py`` 的 ``/api/resume``）。

中间件配置：
- ``FilesystemMiddleware``：读（``ls``/``read_file``/``glob``/``grep``）
  与写（``write_file``/``edit_file``）工具；
- ``FilesystemPermission(mode="interrupt")``：写操作执行前暂停等待人工
  批准。``create_deep_agent(permissions=...)`` 会把 interrupt 规则装配为
  langchain 的 ``HumanInTheLoopMiddleware``。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from deepagents import (
    FilesystemMiddleware,
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents._models import get_model_identifier, get_model_provider
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codepilot.core.llm_utils import safe_content
from codepilot.graphs.sub_graph_data import build_data_analysis_graph
from codepilot.middleware.skillsmiddleware import (
    SkillCatalogLoaderMiddleware,
    SkillCatalogPromptMiddleware,
)
from codepilot.model import init_chat_model
from codepilot.states.workflow_state import WorkflowInput, WorkflowState, user_text

_DEEP_AGENT_NAME = "deep_agent"
_PRELUDE_NAME = "prelude"
_EPILOGUE_NAME = "epilogue"
_INTENT_ROUTE_NAME = "intent_route"
_DATA_ANALYSIS_NAME = "data_analysis"
_SKILLS_DIRECTORY = Path(__file__).resolve().parents[3] / "skills"

# 意图标签（写入 state["intent"]，供路由与 epilogue 使用）
_INTENT_GENERAL = "general"
_INTENT_DATA_ANALYSIS = "data_analysis"

_SYSTEM_PROMPT = """你是 CodePilot 的通用智能助手。

直接完成用户请求；若需要操作工程文件，可以使用当前 DeepAgent 提供的工具。
只输出面向用户的最终答复，不展示内部推理、图结构或执行过程。"""

# 文件系统工具集：读 + 写。写操作由 _FS_PERMISSIONS 交人工审批。
_FS_TOOLS = ["ls", "read_file", "write_file", "edit_file", "glob", "grep"]

# 权限规则：写操作（write_file / edit_file / delete）执行前触发
# HumanInTheLoop 中断，等待 /api/resume 的人工决策；读操作默认放行。
# paths 必须是以 / 开头的锚定绝对路径模式，"/**" 覆盖整个虚拟文件系统。
_FS_PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=["/**"], mode="interrupt"),
]


# ---------------------------------------------------------------------------
# 意图路由：根据用户意图分流到对应执行器（不锁死单一节点）
# ---------------------------------------------------------------------------
# 关键词快速通道：消息里明确提到数据文件时基本可断定是数据分析意图，
# 省一次 LLM 调用。用 lookahead 而非 \b（中文是 \w，"sales.csv里的"
# 这种紧跟中文的场景 \b 不命中）。
_DATA_FILE_PATTERN = re.compile(r"\.(csv|xlsx|xls|jsonl?|tsv)(?![a-zA-Z0-9])", re.IGNORECASE)

_INTENT_CLASSIFY_PROMPT = """你是意图分类器。根据用户消息判断属于哪类意图，只输出以下标签之一，不要输出任何其他内容：

- data_analysis：数据分析类任务。特征：分析/统计/汇总数据、趋势或对比、
  画图表、指标异动归因、经营分析/业务诊断、市场调研/竞对/行业研究、
  撰写分析报告/周报/月报。
- general：其他所有任务。包括编程开发、文件操作、问答闲聊、需求分析、
  质量检查、翻译写作等。

拿不准时输出 general。"""


class IntentRouter:
    """意图路由器：关键词快速通道 → LLM 分类 → 失败兜底 general。

    设计原则（避免锁死）：
    - 任何分类失败（网络异常、输出不可解析）都回退 ``general``，
      保证用户消息总能进入某个执行器，不会因路由而卡死；
    - 关键词快速通道优先于 LLM，减少延迟与误判。
    """

    def __init__(self, model: BaseChatModel | None = None):
        self._model = model or init_chat_model()

    def classify(self, text: str) -> str:
        """返回意图标签：``data_analysis`` 或 ``general``。"""
        if not text:
            return _INTENT_GENERAL
        # 快速通道：明确提到数据文件 → 数据分析意图
        if _DATA_FILE_PATTERN.search(text):
            return _INTENT_DATA_ANALYSIS
        try:
            response = self._model.invoke(
                [
                    SystemMessage(content=_INTENT_CLASSIFY_PROMPT),
                    HumanMessage(content=text),
                ]
            )
            label = safe_content(response).strip().lower()
        except Exception:  # noqa: BLE001 —— 路由失败绝不阻断主流程
            return _INTENT_GENERAL
        # 宽松解析：取首个 token，命中 data_analysis 才切子图，
        # 其余一切（含乱输出）都归 general
        first_token = label.split()[0].strip(".,;:，。；：") if label.split() else ""
        return _INTENT_DATA_ANALYSIS if first_token == _INTENT_DATA_ANALYSIS else _INTENT_GENERAL


def _make_intent_route_node(router: IntentRouter) -> Callable[[WorkflowState], dict[str, Any]]:
    """构造意图路由节点（闭包持有 router 实例，便于测试注入 fake 模型）。"""

    def intent_route_node(state: WorkflowState) -> dict[str, Any]:
        # 前端快捷入口可显式携带 intent（如“数据分析专家”卡片），
        # 此时跳过分类直接进入指定子图；仅接受已知标签，非法值回退自动判定
        forced = str(state.get("intent") or "").strip()
        if forced in (_INTENT_DATA_ANALYSIS, _INTENT_GENERAL):
            return {"intent": forced}
        intent = router.classify(user_text(state))
        return {"intent": intent}

    return intent_route_node


def build_deep_agent(model: Any | None = None) -> CompiledStateGraph:
    """创建官方 DeepAgent 实例：读写文件工具 + 写操作人工审批。"""
    chat_model = model or init_chat_model()
    provider = get_model_provider(chat_model)
    identifier = get_model_identifier(chat_model)
    profile_key = f"{provider}:{identifier}" if provider and identifier else provider
    if not profile_key:
        raise RuntimeError("DeepAgent model must expose a provider for minimal-agent setup")

    # 关闭 deepagents 默认注入的 general-purpose 子代理：最小图只有一个执行器。
    register_harness_profile(
        profile_key,
        HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
    )
    return create_deep_agent(
        model=chat_model,
        system_prompt=_SYSTEM_PROMPT,
        middleware=[
            # _permissions 与下方 permissions 保持一致：中间件实例负责工具
            # 执行期的路径过滤（interrupt 批准后的放行），create_deep_agent
            # 的 permissions 负责装配 HumanInTheLoopMiddleware 的中断配置。
            FilesystemMiddleware(tools=_FS_TOOLS, _permissions=_FS_PERMISSIONS),
            # 扫描 SKILL.md 是昂贵 I/O，只在对话开始时做一次；目录卡片则
            # 在每次模型调用前由内存元数据即时渲染，且不会写入 messages。
            SkillCatalogLoaderMiddleware([_SKILLS_DIRECTORY]),
            SkillCatalogPromptMiddleware(),
        ],
        name=_DEEP_AGENT_NAME,
        subagents=[],
        permissions=_FS_PERMISSIONS,
    )


def _final_reply(state: Mapping[str, Any]) -> str:
    """从共享 messages 通道提取最后一个可见的文本回复。"""
    messages = state.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        text = safe_content(message).strip()
        if text:
            return text
    return ""


def prelude_node(state: WorkflowState) -> dict[str, Any]:
    """把用户输入适配为 DeepAgent 的消息；空输入直接短路。"""
    text = user_text(state)
    if not text:
        return {"chitchat_reply": "请输入具体问题或任务。", "checkpoints": [_PRELUDE_NAME]}
    return {"messages": [HumanMessage(content=text)], "chitchat_reply": ""}


def _route_after_prelude(state: WorkflowState) -> str:
    """空输入短路到 END；非空输入先进入意图路由（不直接锁进某个执行器）。"""
    return _INTENT_ROUTE_NAME if user_text(state) else END


def _route_after_intent(state: WorkflowState) -> str:
    """按意图分流：data_analysis → 数据分析子图；其余 → 通用 DeepAgent。"""
    if state.get("intent") == _INTENT_DATA_ANALYSIS:
        return _DATA_ANALYSIS_NAME
    return _DEEP_AGENT_NAME


def epilogue_node(state: WorkflowState) -> dict[str, Any]:
    """提取最终答复并按实际执行的分支标记检查点。"""
    executor = (
        _DATA_ANALYSIS_NAME if state.get("intent") == _INTENT_DATA_ANALYSIS else _DEEP_AGENT_NAME
    )
    return {
        "chitchat_reply": _final_reply(state) or "本轮未生成可见答复，请稍后重试。",
        "checkpoints": [executor],
    }


def build_main_workflow(
    checkpointer: object | None = None,
    *,
    agent: CompiledStateGraph | None = None,
    data_graph: CompiledStateGraph | None = None,
    router_model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """构建 ``START → prelude → intent_route → {执行器} → epilogue → END`` 主图。

    Args:
        checkpointer: 主图 checkpointer（持久化 + HITL interrupt）。
        agent: 通用执行器（DeepAgent），默认现场构建；测试可注入 fake。
        data_graph: 数据分析执行器（sub_graph_data），默认现场构建；
            测试可注入 fake。
        router_model: 意图分类模型，默认与主图同配置；测试可注入 fake。
    """
    runner = agent or build_deep_agent()
    data_runner = data_graph or build_data_analysis_graph()
    router = IntentRouter(router_model)

    builder = StateGraph(WorkflowState, input_schema=WorkflowInput)
    builder.add_node(_PRELUDE_NAME, prelude_node)
    builder.add_node(_INTENT_ROUTE_NAME, _make_intent_route_node(router))
    # 子图直接挂载：HumanInTheLoop 的 interrupt 冒泡到主图，
    # 由主图 checkpointer 持久化、resume 恢复（手动 invoke 做不到）。
    builder.add_node(_DEEP_AGENT_NAME, runner)
    # 数据分析子图与 deep_agent 同样共享 messages 通道直接挂载
    builder.add_node(_DATA_ANALYSIS_NAME, data_runner)
    builder.add_node(_EPILOGUE_NAME, epilogue_node)
    builder.add_edge(START, _PRELUDE_NAME)
    builder.add_conditional_edges(
        _PRELUDE_NAME,
        _route_after_prelude,
        {_INTENT_ROUTE_NAME: _INTENT_ROUTE_NAME, END: END},
    )
    # 意图分流：不锁死单一执行器，按 intent 选择 data_analysis / deep_agent
    builder.add_conditional_edges(
        _INTENT_ROUTE_NAME,
        _route_after_intent,
        {_DATA_ANALYSIS_NAME: _DATA_ANALYSIS_NAME, _DEEP_AGENT_NAME: _DEEP_AGENT_NAME},
    )
    builder.add_edge(_DEEP_AGENT_NAME, _EPILOGUE_NAME)
    builder.add_edge(_DATA_ANALYSIS_NAME, _EPILOGUE_NAME)
    builder.add_edge(_EPILOGUE_NAME, END)
    return builder.compile(checkpointer=checkpointer)


@asynccontextmanager
async def create_graph(_config: RunnableConfig) -> AsyncIterator[CompiledStateGraph]:
    """供 LangGraph Platform 加载的图工厂。

    延迟构建可避免模块导入阶段读取模型密钥；Platform 在已加载环境变量后
    进入该上下文，FastAPI 则直接调用 ``build_main_workflow``。
    """
    yield build_main_workflow()
