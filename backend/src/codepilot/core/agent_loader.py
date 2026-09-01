"""将 Agent Harness 定义（``agents/*.yaml``）加载为可执行的 Runnable。

``backend/agents/``（以及 ``backend/agents/review_panels/``）下的每个 YAML 文件
都声明了单个 Agent 的 Harness：其角色提示词、允许使用的工具，以及预期的
输出 schema。本模块将这些声明式配置解析为 LangChain 的 ``Runnable``，
供图节点直接调用 ``.invoke()``。
"""

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from codepilot.core.create_model import create_chat_model
from codepilot.tools import (
    deploy_demo,
    query_sql,
    screenshot_diff,
    search_km,
    vector_memory,
)

# 将工具名称（即 agents/*.yaml 中 `tools:` 引用的名称）映射到实际
# LangChain 工具对象的注册表。新增工具时需要扩展此处。
_TOOL_REGISTRY: dict[str, BaseTool] = {
    "search_km": search_km,
    "query_sql": query_sql,
    "screenshot_diff": screenshot_diff,
    "deploy_demo": deploy_demo,
    "vector_memory": vector_memory,
}

# backend/ 根目录，即 src/codepilot/ 的上级目录
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_DIR = _BACKEND_ROOT / "agents"


class AgentConfigError(RuntimeError):
    """当 Agent Harness 的 YAML 文件缺失或格式错误时抛出。"""


@dataclass(frozen=True)
class AgentHarness:
    """一个已解析的 Agent Harness：角色提示词 + 绑定的工具 + 输出 schema。"""

    name: str
    description: str
    system_prompt: str
    tool_names: list[str] = field(default_factory=list)
    output_schema: dict[str, Any] = field(default_factory=dict)
    # 额外的 Harness 专属字段（例如评审面板的 `perspective`）。
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tools(self) -> list[BaseTool]:
        """将配置的工具名称解析为实际的 LangChain 工具对象。"""
        resolved = []
        for tool_name in self.tool_names:
            tool_obj = _TOOL_REGISTRY.get(tool_name)
            if tool_obj is None:
                raise AgentConfigError(
                    f"Agent '{self.name}' references unknown tool '{tool_name}'. "
                    f"Known tools: {sorted(_TOOL_REGISTRY)}"
                )
            resolved.append(tool_obj)
        return resolved


def _resolve_yaml_path(harness_ref: str) -> Path:
    """将 Harness 引用（名称或相对路径）解析为绝对路径。

    Args:
        harness_ref: 可以是一个纯名称（如 ``"research"``，会被解析为
            ``agents/research.yaml``），也可以是相对于 ``agents/`` 的路径
            （如 ``"review_panels/platform.yaml"``）。

    Returns:
        该 YAML 文件的绝对路径。
    """
    candidate = Path(harness_ref)
    if candidate.suffix != ".yaml":
        candidate = candidate.with_suffix(".yaml")
    if not candidate.is_absolute():
        candidate = _AGENTS_DIR / candidate
    return candidate


@cache
def load_agent_harness(harness_ref: str) -> AgentHarness:
    """加载并解析一个 Agent Harness 的 YAML 文件。

    Args:
        harness_ref: 纯 Harness 名称（``"research"``、``"data"``、``"design"``、
            ``"qa"``），或相对于 ``agents/`` 的路径
            （``"review_panels/platform.yaml"``）。

    Returns:
        解析得到的 :class:`AgentHarness`。

    Raises:
        AgentConfigError: 当文件缺失或格式错误时。
    """
    path = _resolve_yaml_path(harness_ref)
    if not path.exists():
        raise AgentConfigError(f"Agent Harness config not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AgentConfigError(f"Failed to parse Agent Harness YAML {path}: {exc}") from exc

    if "name" not in raw:
        raise AgentConfigError(f"Agent Harness config {path} is missing required field 'name'")

    known_fields = {"name", "description", "system_prompt", "tools", "output_schema", "perspective"}
    extra = {k: v for k, v in raw.items() if k not in known_fields}

    return AgentHarness(
        name=raw["name"],
        description=raw.get("description", ""),
        system_prompt=raw.get("system_prompt", ""),
        tool_names=list(raw.get("tools") or []),
        output_schema=dict(raw.get("output_schema") or {}),
        extra=extra,
    )


def build_agent_runnable(
    harness_ref: str,
    model_name: str | None = None,
    model: BaseChatModel | None = None,
) -> Runnable:
    """构建一个绑定了 Agent Harness 工具的 Runnable 对话模型。

    Args:
        harness_ref: 纯 Harness 名称或相对于 ``agents/`` 的路径。
        model_name: 传递给 :func:`create_chat_model` 的可选模型标识符。
            如果提供了 ``model``，则忽略此参数。
        model: 可复用的预先构建好的对话模型实例（例如避免每次
            节点调用都重新实例化客户端）。

    Returns:
        一个 Runnable，给定输入消息列表后，返回一个可能包含工具调用的
        AIMessage。调用方需自行在使用时在前面拼接 Harness 的
        ``SystemMessage``，除非使用 ``invoke_agent`` 而不是直接驱动该 Runnable。
    """
    harness = load_agent_harness(harness_ref)
    chat_model = model or create_chat_model(model_name)
    tools = harness.tools
    return chat_model.bind_tools(tools) if tools else chat_model


def invoke_agent(
    harness_ref: str,
    user_input: str,
    model_name: str | None = None,
    model: BaseChatModel | None = None,
) -> Any:
    """端到端地调用一个 Agent Harness，完成一次单轮请求。

    将 Harness 的 ``system_prompt`` 作为 ``SystemMessage`` 拼接在前面，
    并将 ``user_input`` 作为 ``HumanMessage`` 发送。这是一个便捷封装，
    适用于只需要简单一次性调用的节点函数；多轮对话或工具执行循环
    应在 :func:`build_agent_runnable` 基础上自行构建。

    Args:
        harness_ref: 纯 Harness 名称或相对于 ``agents/`` 的路径。
        user_input: 用户/任务消息内容。
        model_name: 可选的模型标识符。
        model: 可复用的预先构建好的对话模型实例。

    Returns:
        底层对话模型返回的 AIMessage。
    """
    harness = load_agent_harness(harness_ref)
    runnable = build_agent_runnable(harness_ref, model_name=model_name, model=model)
    messages = [SystemMessage(content=harness.system_prompt), HumanMessage(content=user_input)]
    return runnable.invoke(messages)
