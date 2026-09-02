"""将 Agent Harness 定义（``agents/*.yaml``）加载为可执行的 Runnable。

``backend/agents/``（以及 ``backend/agents/review_panels/``）下的每个 YAML 文件
都声明了单个 Agent 的 Harness：其角色提示词、允许使用的工具，以及预期的
输出 schema。本模块将这些声明式配置解析为 LangChain 的 ``Runnable``，
供图节点直接调用 ``.invoke()``。
"""

import json
import logging
import concurrent.futures
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from codepilot.core.create_model import create_chat_model
from codepilot.core.llm_utils import extract_json, safe_content
from codepilot.core.memory_store import load_agent_memory
from codepilot.states.models import model_from_output_schema, validate_harness_output
from codepilot.tools import (
    browser_screenshot,
    deploy_demo,
    mcp_call,
    pde_prototype,
    python_repl,
    query_sql,
    screenshot_diff,
    search_km,
    vector_memory,
)

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 8
_LLM_TIMEOUT_SEC = 150  # 兜底超时，防止 LongCat 等模型调用永久挂起

# 将工具名称（即 agents/*.yaml 中 `tools:` 引用的名称）映射到实际
# LangChain 工具对象的注册表。新增工具时需要扩展此处。
_TOOL_REGISTRY: dict[str, BaseTool] = {
    "search_km": search_km,
    "query_sql": query_sql,
    "screenshot_diff": screenshot_diff,
    "browser_screenshot": browser_screenshot,
    "deploy_demo": deploy_demo,
    "vector_memory": vector_memory,
    "python_repl": python_repl,
    "mcp_call": mcp_call,
    "pde_prototype": pde_prototype,
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
    eval_cases: list[dict[str, Any]] = field(default_factory=list)
    # 额外的 Harness 专属字段（例如评审面板的 `perspective`）。
    extra: dict[str, Any] = field(default_factory=dict)
    state_fields: list[str] = field(default_factory=list)

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


def _filter_tools(tools: list[BaseTool], allowed_tools: Sequence[str] | None) -> list[BaseTool]:
    """只保留白名单里的工具；``None`` 表示不裁剪，空列表表示本轮不绑定工具。"""
    if allowed_tools is None:
        return tools
    allow = {name for name in allowed_tools if name}
    return [tool for tool in tools if tool.name in allow]


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


@lru_cache(maxsize=32)
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

    known_fields = {
        "name",
        "description",
        "system_prompt",
        "tools",
        "output_schema",
        "perspective",
        "eval",
        "state_fields",
    }
    extra = {k: v for k, v in raw.items() if k not in known_fields}
    eval_block = raw.get("eval") or {}
    eval_cases = list(eval_block.get("cases") or []) if isinstance(eval_block, dict) else []

    return AgentHarness(
        name=raw["name"],
        description=raw.get("description", ""),
        system_prompt=raw.get("system_prompt", ""),
        tool_names=list(raw.get("tools") or []),
        output_schema=dict(raw.get("output_schema") or {}),
        extra=extra,
        eval_cases=eval_cases,
        state_fields=[str(item) for item in (raw.get("state_fields") or [])],
    )


def build_agent_runnable(
    harness_ref: str,
    model_name: str | None = None,
    model: BaseChatModel | None = None,
    allowed_tools: Sequence[str] | None = None,
) -> Runnable:
    """构建一个绑定了 Agent Harness 工具的 Runnable 对话模型。

    Args:
        harness_ref: 纯 Harness 名称或相对于 ``agents/`` 的路径。
        model_name: 传递给 :func:`create_chat_model` 的可选模型标识符。
            如果提供了 ``model``，则忽略此参数。
        model: 可复用的预先构建好的对话模型实例（例如避免每次
            节点调用都重新实例化客户端）。
        allowed_tools: 本轮允许绑定的工具名；``None`` 使用 Harness 全量。

    Returns:
        一个 Runnable，给定输入消息列表后，返回一个可能包含工具调用的
        AIMessage。调用方需自行在使用时在前面拼接 Harness 的
        ``SystemMessage``，除非使用 ``invoke_agent`` 而不是直接驱动该 Runnable。
    """
    harness = load_agent_harness(harness_ref)
    chat_model = model or create_chat_model(model_name)
    tools = _filter_tools(harness.tools, allowed_tools)
    return chat_model.bind_tools(tools) if tools else chat_model


class _LLMTimeoutError(TimeoutError):
    """LLM 调用超时兜底异常。"""


def _invoke_with_timeout(runnable: Runnable, messages: list[Any], seconds: int) -> Any:
    """在独立线程中调用 ``runnable.invoke``，超时后抛出 ``_LLMTimeoutError``。

    ``ChatOpenAI.timeout`` 参数在部分兼容接口（如 LongCat）上可能不生效，
    导致 HTTP 连接永久挂起。此函数作为最后一道防线，
    使用 ``concurrent.futures.ThreadPoolExecutor`` 实现超时，
    兼容主线程和工作线程场景（不受 SIGALRM 限制）。
    """
    import os
    if os.getenv("CODEPILOT_DISABLE_TIMEOUT_GUARD", "").strip():
        return runnable.invoke(messages)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ctx:
        future = ctx.submit(runnable.invoke, messages)
        try:
            return future.result(timeout=seconds)
        except concurrent.futures.TimeoutExpired:
            raise _LLMTimeoutError(f"LLM call timed out after {seconds}s")


def _serialize_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except TypeError:
        return str(result)


def _run_tool_loop(runnable: Runnable, tools: list[BaseTool], messages: list[Any]) -> Any:
    """执行 bind_tools 之后的 tool_calls，直到模型给出最终回复。"""
    tool_by_name = {tool.name: tool for tool in tools}
    last_response: Any = None
    for round_index in range(_MAX_TOOL_ROUNDS):
        try:
            last_response = _invoke_with_timeout(runnable, messages, _LLM_TIMEOUT_SEC)
        except _LLMTimeoutError:
            logger.warning("invoke_agent: round %d LLM call timed out (%ds)", round_index + 1, _LLM_TIMEOUT_SEC)
            break
        messages.append(last_response)
        tool_calls = getattr(last_response, "tool_calls", None) or []
        if not tool_calls:
            return last_response
        logger.info(
            "invoke_agent: round %d executing %d tool call(s): %s",
            round_index + 1,
            len(tool_calls),
            [call.get("name") for call in tool_calls],
        )
        for call in tool_calls:
            name = call.get("name", "")
            tool_obj = tool_by_name.get(name)
            call_id = call.get("id") or f"{name}-{uuid.uuid4().hex[:8]}"
            try:
                if tool_obj is None:
                    result: Any = {"error": f"unknown tool '{name}'"}
                else:
                    result = tool_obj.invoke(call.get("args") or {})
            except Exception as exc:  # noqa: BLE001 - 单次工具失败不得中断整个循环
                logger.exception("invoke_agent: tool %s failed", name)
                result = {"error": str(exc)}
            messages.append(
                ToolMessage(content=_serialize_tool_result(result), tool_call_id=call_id)
            )
    logger.warning("invoke_agent: hit max tool rounds (%d), returning last model message", _MAX_TOOL_ROUNDS)
    return last_response


def _rewrite_content(response: Any, payload: dict[str, Any]) -> Any:
    encoded = json.dumps(payload, ensure_ascii=False)
    if hasattr(response, "content"):
        try:
            response.content = encoded
            return response
        except Exception:  # noqa: BLE001
            logger.debug("invoke_agent: could not rewrite response.content")
    return encoded


def _apply_output_schema(
    harness: AgentHarness,
    response: Any,
    chat_model: BaseChatModel,
    messages: list[Any],
) -> Any:
    """用 Pydantic 校验 output_schema；失败时再试 with_structured_output。"""
    if not harness.output_schema:
        return response
    parsed = extract_json(safe_content(response))
    payload: Any = parsed if isinstance(parsed, dict) else {}
    try:
        validated = validate_harness_output(harness.name, harness.output_schema, payload)
        return _rewrite_content(response, validated)
    except Exception:
        logger.info("invoke_agent: schema validation missed, trying with_structured_output")
    try:
        model_cls = model_from_output_schema(harness.name, harness.output_schema)
        structured = chat_model.with_structured_output(model_cls)
        try:
            result = _invoke_with_timeout(structured, messages, _LLM_TIMEOUT_SEC)
        except _LLMTimeoutError:
            logger.warning("invoke_agent: structured_output call timed out (%ds)", _LLM_TIMEOUT_SEC)
            raise
        if hasattr(result, "model_dump"):
            validated = result.model_dump()
        elif isinstance(result, dict):
            validated = result
        else:
            raise TypeError(type(result))
        return _rewrite_content(response, validated)
    except Exception:
        logger.exception("invoke_agent: with_structured_output failed for %s", harness.name)
        fallback = {
            key: payload.get(key) if isinstance(payload, dict) else None
            for key in harness.output_schema
        }
        return _rewrite_content(response, fallback)


def invoke_agent(
    harness_ref: str,
    user_input: str,
    model_name: str | None = None,
    model: BaseChatModel | None = None,
    allowed_tools: Sequence[str] | None = None,
) -> Any:
    """端到端调用一个 Agent Harness，并在有工具时跑完 tool-call 循环。

    将 Harness 的 ``system_prompt`` 作为 ``SystemMessage`` 拼接在前面，
    注入该 Agent 的分域记忆，再发送 ``user_input``。若模型返回
    ``tool_calls``，则执行对应工具并把结果作为 ``ToolMessage`` 回灌，
    直到模型给出最终回复或达到轮次上限。

    Args:
        harness_ref: 纯 Harness 名称或相对于 ``agents/`` 的路径。
        user_input: 用户/任务消息内容。
        model_name: 可选的模型标识符。
        model: 可复用的预先构建好的对话模型实例。
        allowed_tools: 本轮允许调用的工具名；用于按需求裁剪 Harness 工具集。

    Returns:
        底层对话模型返回的最终 AIMessage。
    """
    harness = load_agent_harness(harness_ref)
    chat_model = model or create_chat_model(model_name)
    runnable = build_agent_runnable(
        harness_ref,
        model_name=model_name,
        model=chat_model,
        allowed_tools=allowed_tools,
    )
    memory = {}
    try:
        memory = load_agent_memory(harness.name)
    except Exception:  # noqa: BLE001
        logger.exception("invoke_agent: failed to load agent memory for %s", harness.name)

    content = user_input
    if memory:
        content = (
            f"{user_input}\n\n[agent_memory:{harness.name}]\n"
            f"{json.dumps(memory, ensure_ascii=False)}"
        )
    messages: list[Any] = [
        SystemMessage(content=harness.system_prompt),
        HumanMessage(content=content),
    ]
    tools = _filter_tools(harness.tools, allowed_tools)
    if tools:
        response = _run_tool_loop(runnable, tools, messages)
    else:
        try:
            response = _invoke_with_timeout(runnable, messages, _LLM_TIMEOUT_SEC)
        except _LLMTimeoutError:
            logger.warning("invoke_agent: %s LLM call timed out (%ds)", harness.name, _LLM_TIMEOUT_SEC)
            raise
    return _apply_output_schema(harness, response, chat_model, messages)
