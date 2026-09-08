"""数据分析子图：``START → call_model ⇄ execute_tools → summarize → END``。

一个经典的 Tool-Calling Agent 循环（ReAct 风格）：

- ``call_model``：LLM 读取对话历史，决定"直接回答"还是"调用工具"；
- ``execute_tools``：执行模型下发的工具调用（数据探索 / 过滤 /
  聚合 / 画图，见 ``codepilot.tools.analyze_data``）；
- ``summarize``：模型不再请求工具时，提取最终分析报告并收尾。

循环由 ``_route_after_model`` 条件边驱动：AIMessage 带 ``tool_calls``
就进工具节点，工具结果以 ToolMessage 追加回 ``messages`` 通道后
再次回到 ``call_model``，直到模型产出不含工具调用的最终答复。

状态设计
========
复用 LangGraph 官方消息模式：``messages`` 用 ``add_messages`` reducer
累积（新消息按 ID 合并而非整体覆盖），与主图 ``WorkflowState`` 的
共享通道语义一致，便于后续把本子图直接挂载为主图节点。

工具安全
========
工具集来自 ``codepilot.tools.__tools_data__``：前四个（analyze_data /
filter_rows / group_aggregate / plot_chart）是白名单化的本地原子操作，
不执行 LLM 下发的任意代码；输出统一截断，防止撑爆上下文。第五个
ba_agent_analysis 是兜底工具——仅当本地工具无法解决（需外部业务
数据、行业研究、经营诊断、完整报告等）时，转交 BA-Agent 后端
（backend/skills/data_analyze，baa-basic skill）分析。

用法示例
========
::

    from codepilot.graphs.sub_graph_data import graph

    result = graph.invoke({
        "messages": [("user", "分析 sales.csv，哪个城市销售额最高？画个柱状图")]
    })
    final = result["messages"][-1].content
"""

from collections.abc import Callable
from typing import Annotated, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from codepilot.model import init_chat_model
from codepilot.tools import __tools_data__

# ---------------------------------------------------------------------------
# 系统提示词：定义分析助手角色与工具选择策略（含 baa-basic 兜底策略）
# ---------------------------------------------------------------------------
# 关键约束：本地工具优先，ba_agent_analysis（baa-basic skill / BA-Agent
# 后端）仅作能力兜底——它走美团内网远端链路，耗时且依赖 SSO 登录态，
# 若每个问题都优先调它会显著拖慢响应。
_SYSTEM_PROMPT = """你是 CodePilot 的数据分析助手，负责解答用户的数据分析问题。

你可以调用两类工具，请严格按以下优先级选择：

1.【本地工具，优先使用】处理用户已提供的数据文件（.csv/.xlsx/
   .xls/.json/.jsonl）：
   - analyze_data：数据概况探索（EDA）与定向分析，拿到文件后首先调用它了解数据
   - filter_rows：按条件筛选行
   - group_aggregate：分组聚合（求和/均值/计数等）
   - plot_chart：绘制折线/柱状/散点/直方图

2.【兜底工具，仅当本地工具无法解决时使用】ba_agent_analysis：把问题
   转交给 BA-Agent 商业分析后端（baa-basic skill）。适用场景：
   - 需要外部业务数据、市场调研、行业研究、竞对分析
   - 经营分析/业务诊断、指标异动归因
   - OKR/KPI 复盘、撰写完整分析报告/周报/月报
   本地数据文件的常规探索/过滤/聚合/画图一律用本地工具，禁止用
   ba_agent_analysis。对同一话题追问时，把上一次返回的 conversation_id
   传回可复用上下文。BA-Agent 需要美团内网 SSO 登录态，鉴权失败时
   会返回引导信息，如实转告用户即可。

回答要求：
- 给出结论时引用工具产出（表格/图表路径/关键数字），不臆造数据
- 工具报错时如实说明错误与建议，不用编造内容填补"""

# ---------------------------------------------------------------------------
# 子图状态定义
# ---------------------------------------------------------------------------
class DataAnalysisState(TypedDict):
    """数据分析子图的状态。

    Attributes:
        messages: 对话消息通道。用户提问、模型回复（含 tool_calls）、
            工具执行结果（ToolMessage）全部经 ``add_messages`` 累积，
            是驱动 Agent 循环的唯一上下文。
    """

    messages: Annotated[list[AnyMessage], add_messages]


# ---------------------------------------------------------------------------
# 节点函数 1：LLM 决策节点
# ---------------------------------------------------------------------------
def _make_call_model(
    model: BaseChatModel,
) -> Callable[[DataAnalysisState], dict[str, list[AnyMessage]]]:
    """绑定工具并创建决策节点，避免模块导入时读取生产模型密钥。"""
    model_with_tools = model.bind_tools(__tools_data__)

    def call_model(state: DataAnalysisState) -> dict[str, list[AnyMessage]]:
        """调用绑定了工具的 LLM，生成下一动作（回答或工具调用）。"""
        messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    return call_model


# ---------------------------------------------------------------------------
# 节点函数 2：工具执行节点
# ---------------------------------------------------------------------------
# ToolNode 读取上一条 AIMessage 的 tool_calls，逐个调用对应工具，
# 并把每个结果包装为 ToolMessage 追加进 messages 通道。
# 工具已在 analyze_data.py 中注册了异步 coroutine，异步执行时
# （astream / ainvoke）pandas 计算会进线程池，不阻塞事件循环。
execute_tools = ToolNode(__tools_data__)


# ---------------------------------------------------------------------------
# 节点函数 3：总结收尾节点
# ---------------------------------------------------------------------------
def summarize(state: DataAnalysisState) -> dict[str, list[AnyMessage]]:
    """总结收尾节点：标记循环结束，不追加新消息。

    进入本节点意味着模型已不再请求工具，最后一条 AIMessage 即为
    面向用户的最终结论（含 Markdown 表格 / 图表路径等工具产出引用）。
    调用方可直接读取 ``result["messages"][-1].content``，或使用
    下方的 ``_extract_final_reply`` 反向查找非空文本。
    """
    return {"messages": []}


def _extract_final_reply(state: DataAnalysisState) -> str:
    """从 messages 通道反向查找最后一条非空的 AI 文本回复。

    跳过 ToolMessage 与空 content（纯 tool_calls 的 AIMessage），
    保证拿到的确实是最终分析结论。
    """
    for message in reversed(state["messages"]):
        if isinstance(message, ToolMessage):
            continue
        text = (getattr(message, "content", "") or "").strip()
        if text:
            return text
    return ""


# ---------------------------------------------------------------------------
# 路由：模型输出后决定「执行工具」还是「总结结束」
# ---------------------------------------------------------------------------
def _route_after_model(state: DataAnalysisState) -> str:
    """条件边：AIMessage 带 tool_calls → execute_tools；否则 → summarize。"""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "execute_tools"
    return "summarize"


# ---------------------------------------------------------------------------
# 编译子图
# ---------------------------------------------------------------------------
def build_data_analysis_graph(model: BaseChatModel | None = None) -> CompiledStateGraph:
    """构建并编译数据分析子图。

    模型在此处而非模块导入阶段初始化，保证测试、静态检查和服务探针
    不需要读取生产密钥；实际执行图时仍会通过 ``init_chat_model`` 校验配置。

    拓扑::

        START → call_model ──(有 tool_calls)──→ execute_tools ─┐
                     ↑                                       │
                     └───────────────────────────────────────┘
                     └──(无 tool_calls)──→ summarize → END
    """
    chat_model = model or init_chat_model()
    builder = StateGraph(DataAnalysisState)
    builder.add_node("call_model", _make_call_model(chat_model))
    builder.add_node("execute_tools", execute_tools)
    builder.add_node("summarize", summarize)
    builder.add_edge(START, "call_model")
    builder.add_conditional_edges(
        "call_model",
        _route_after_model,
        {"execute_tools": "execute_tools", "summarize": "summarize"},
    )
    # 工具结果写回 messages 后回到模型节点，形成 Agent 循环
    builder.add_edge("execute_tools", "call_model")
    builder.add_edge("summarize", END)
    return builder.compile()


def graph() -> CompiledStateGraph:
    """兼容直接导入的图工厂；调用时才初始化模型。"""
    return build_data_analysis_graph()
