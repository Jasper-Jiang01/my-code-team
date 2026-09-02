---
name: langgraph-skill
description: 使用 Python LangGraph 开发、改造、排查、测试或评审有状态 Agent/工作流。凡是用户要求创建或修改 StateGraph、Agent 图、节点、边、条件路由、工具调用循环、Reducer、Command、Send 动态扇出、子图、多 Agent、checkpointer/线程、interrupt 人在回路、流式输出、长期记忆、LangGraph Server/API 或 LangGraph 部署时，都应优先使用本技能；即使用户只说“做个工作流 Agent”“给 Agent 加工具/审批/记忆/分支/并行”“LangGraph 报错”“把链式 Agent 改成图”，也应触发。仅解释与 LangGraph 无关的通用 Python 代码时不使用。
---

# LangGraph 开发

将 LangGraph 当成有状态、可检查点恢复的图执行引擎，而不是把所有逻辑塞进一个聊天循环。优先把业务流程拆成边界清晰、输入输出明确、可重入的节点；将短期状态、运行时上下文、跨线程记忆和外部副作用分开建模。

本技能基于用户提供的 LangGraph Python 教学工程归纳，工程固定使用 `langgraph==1.2.10`。实际动手前先检查目标项目中锁定的 `langgraph`、`langchain-core` 与 Python 版本；若 API 有差异，以项目当前依赖及其本地类型/文档为准，不要混用不同大版本的示例。

## 先判断任务类型

先从用户需求和仓库现状判断需要哪种图，而不是一开始就写代码：简单线性步骤使用普通边；依据状态或模型结果选分支使用条件边；模型调用工具再继续推理使用 Agent 工具循环；要向多个工作单元分发任务时使用 `Send` 动态扇出；需要局部封装或不同状态域时使用子图；必须经人确认、编辑或批准时使用 `interrupt`；需要跨调用继续执行时使用 checkpointer 和稳定的 `thread_id`。

用户仅询问概念、示例或设计方案时，不要创建、修改或删除项目文件。用户明确要求实现、修复、重构或新增功能时，先阅读相关代码、依赖定义及现有测试，再做最小且符合原有架构的变更。不要擅自执行 Git 操作。

## 开发前检查

先完成以下检查，再开始改动：

1. 确认工作区、Python 版本和依赖管理方式。用户提供的参考工程使用 UV；已有 UV 项目中安装依赖用 `uv add <package>`，运行用 `uv run ...`，不要混用 `pip` 破坏锁文件。
2. 阅读 `pyproject.toml` 或等价配置，确认 LangGraph 版本；定位 `StateGraph` 构建点、状态定义、节点、工具、编译点、运行入口和测试。
3. 明确图契约：初始输入、每个状态字段的所有者与 reducer、节点可返回的局部更新、终止条件、线程标识、是否持久化、外部副作用和人类恢复数据。
4. 对涉及模型或外部服务的变更，确认环境变量、模型提供商、权限及安全边界；不要把密钥写入代码、日志或版本控制文件。
5. 写代码前先用简洁自然语言向用户说明图结构、状态粒度和完成标准；需求有不可自行决定的业务分支时，只问一个最关键的问题。

## 状态、上下文与记忆

使用 `TypedDict` 定义图的短期状态。状态字段是并发超步之间的共享 channel：默认语义是覆盖；需要累积、聚合或去重时必须明确 reducer。消息列表应使用 `add_messages`，避免用普通列表覆盖历史消息，也借助消息 ID 支持原位更新。

```python
from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    attempts: int
    draft: NotRequired[str]
```

将每次调用可变但不应写入检查点的配置放进 `context_schema`，例如模型选择、温度、启用工具列表、调用方身份或租户标识。节点通过当前项目使用的 `Runtime` 注入方式或 `get_runtime(...).context` 读取。不要假设底层聊天模型会自动读取 LangGraph context：如果模型自身只识别 `RunnableConfig["configurable"]`，应在模型节点中显式、安全地映射允许传递的字段（参考工程写法：`get_runtime(ContextSchema).context` 取值后，把模型相关字段转成 `config={"configurable": {...}}` 传入 `ainvoke`）。

区分三层数据：图状态用于同一线程的当前任务；checkpointer 用于同一线程的暂停与恢复；store 用于跨线程、跨任务的长期记忆。长期记忆读写之前先校验 store 可用以及 `user_id`/tenant 等命名空间标识存在，禁止让不同用户共享同一命名空间。

## 图构建规范

构建函数只负责声明图，入口模块集中编译和导出编译后的图。为每个图声明入口与出口，节点命名使用稳定、业务化的动词，例如 `validate_request`、`call_model`、`review_result`。节点返回局部状态增量，不要原样返回整个状态，除非确有意覆盖的意图。

```python
from langgraph.graph import END, START, StateGraph


def classify(state: AgentState) -> dict:
    last = state["messages"][-1].content
    return {"draft": str(last).strip()}


def route_after_classify(state: AgentState) -> str:
    return "review" if state.get("draft") else "stop"


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("classify", classify)
    builder.add_node("review", review)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {"review": "review", "stop": END},
    )
    builder.add_edge("review", END)
    return builder
```

条件路由函数的返回值必须与 `add_conditional_edges` 的路径映射一致。若路由返回节点名，映射也应覆盖该值；若可能返回 `END`，显式包含它。编译后的图不可再增删节点或边，因此先完成声明，再调用 `compile()`。

为已知业务错误设计可读的处理路径。可以通过 `set_node_defaults(error_handler=...)` 设置节点默认错误处理器，把错误整理成状态消息并结束；对明确可瞬态失败的节点可配置 `RetryPolicy`（有界次数）、`TimeoutPolicy`（运行/空闲超时）。不要对非幂等写操作、参数错误、鉴权失败或中断恢复自动无限重试；错误需保留节点名、可行动原因和安全的上下文，不泄露密钥或个人数据。

## Agent 与工具调用循环

模型节点负责绑定允许的工具、调用模型并向 `messages` 追加一条 `AIMessage`；工具节点负责执行工具；路由节点依据最后一条 AI 消息是否包含 `tool_calls` 决定进入工具节点还是结束。工具完成后回到模型节点。工具清单应由白名单或上下文配置过滤，不能把未授权工具全部暴露给模型。

```python
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage


def route_tools(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


def build_agent_graph(tools):
    builder = StateGraph(AgentState)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_tools, {"tools": "tools", END: END})
    builder.add_edge("tools", "model")
    return builder
```

若需要并行执行多个工具调用，可以针对每个 tool call 返回 `Send("tools", [tool_call])`，但必须确认工具函数、状态 reducer 与下游写入在并发下安全。工具返回 `ToolMessage` 时，其 `tool_call_id` 必须对应触发它的 AI tool call 的 ID。工具应使用精确的 Pydantic/JSON schema；对不合法输入返回可供模型修正的错误，避免默默吞掉异常。

需要从工具或节点同时更新状态并选择后继时使用 `Command`。在返回类型中使用 `Command[Literal[...]]`（适用时）表达可能的跳转，提高静态检查能力。牢记静态边和 `Command(goto=...)` 的动态边都会执行：只有在确实希望两个路径并存时才同时定义，避免意外重复调用。

```python
from typing import Literal
from langgraph.types import Command


def approve(state: AgentState) -> Command[Literal["execute", "revise"]]:
    if is_valid(state):
        return Command(update={"attempts": 0}, goto="execute")
    return Command(update={"attempts": state["attempts"] + 1}, goto="revise")
```

## Checkpointer、线程与中断

只要用户需要多轮对话、恢复、时间旅行、审批、人工编辑或长期运行任务，就在编译时提供 checkpointer，并在每次调用的 `configurable` 中使用稳定、唯一的 `thread_id`。开发验证可使用内存 checkpointer；生产选择可靠的持久化后端并设置数据隔离、保留和清理策略。不要在每轮请求随机生成 `thread_id`，否则无法恢复既有执行。

```python
from langgraph.checkpoint.memory import InMemorySaver

compiled_graph = build_graph().compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "case-123"}}
result = compiled_graph.invoke(input_state, config=config)
```

将 `interrupt(payload)` 放在需要人类决定的位置，并令 payload 是稳定、可渲染的结构化契约。用 `Command(resume=value)` 以同一 `thread_id` 恢复。节点从中断恢复时会从节点开头重新运行，因此中断前的写库、发消息、支付或文件写入必须幂等、可去重或移到可控的提交节点；不要在 broad `try/except` 中捕获并吞掉中断。

多次中断必须保持数量与执行顺序稳定。不要根据前一次回答随意增加、删除或重排同一节点内的 interrupt；若流程确实变化，将其拆为显式节点。对必须暂停的业务操作，先暂停再执行副作用。

## 子图、多 Agent 与并行

需要封装一段流程、复用工作流或隔离状态时使用子图。父子状态相同可把已编译子图直接作为节点；状态不同则用包装节点映射输入与输出。先设计清楚父子边界和 reducer，不能依赖同名字段的偶然兼容。

默认每次调用的子图使用 `compile(checkpointer=None)`：它会在当前父图调用中继承父图检查点，从而支持 interrupt。`checkpointer=True` 表示子图按线程保留独立状态；`False` 表示无状态，不能中断或持久恢复。将 `Command(graph=Command.PARENT, goto=...)` 限制在子图需要显式返回父图控制流的场景。

“多 Agent”不应默认意味着多张复杂图。若仅是角色、提示词、工具集合或模型参数不同，可复用同一主图，通过 context 区分角色，并用一个受控工具或节点调用子 Agent。只有当角色拥有不同的状态、循环、持久化或可独立测试的流程时才拆成子图。主图必须限制递归深度、总调用次数、并行数和单次预算，避免代理互相调用失控。

大量相互独立的工作项使用 `Send` 动态扇出至 worker，再通过 reducer 汇总结果。worker 不应写同一个无 reducer 的字段；外部副作用必须附带任务 ID 并支持幂等重试。需要并行中断时，验证每个分支对应的恢复数据和恢复顺序。

## 流式、异步与运行控制

I/O 密集节点优先实现 `async def` 并使用 `await graph.ainvoke(...)` 或 `async for` 消费 `astream(...)`。不要在异步节点中调用阻塞 SDK；如不可避免，使用项目既有线程池/异步适配方式。

为不同消费者明确选择流模式：`updates` 用于节点的局部增量，`values` 用于每个超步后的完整状态，`messages` 或 `messages-tuple` 用于 token/消息流，`checkpoints` 用于调试恢复点。流式客户端必须识别 interrupt、错误和取消状态，且不可仅因收到一个 chunk 就将工作标记成功。

对循环图设置清晰终止条件和合理递归/步数上限。支持取消时，在节点之间检查取消信号，并让长操作可中止；不要将不可取消的长任务隐藏在一个单一节点中。

## 测试、调试与交付

先为图逻辑建立与模型供应商无关的测试。用假模型或固定模型响应覆盖：无工具的成功路径、单工具路径、连续工具调用、条件路由的每个分支、无效工具参数、节点失败、线程恢复、interrupt/resume、并行汇总和递归上限。测试时断言最终状态、状态历史或执行顺序，而非只断言自然语言文本。

每次代码改动后执行项目已有最窄相关测试，再运行格式化、类型检查和完整测试命令（若项目具备）。如果没有测试框架，至少添加或运行一个可重复的最小 smoke test，验证：图可编译，基础输入可终止，带工具的输入会返回模型节点，interrupt 可用同一 thread 恢复，持久化配置不会跨用户串数据。

调试时输出安全的结构化摘要，例如节点名、线程 ID、运行 ID、状态字段名和错误类别；不要记录完整提示词、访问令牌、用户隐私或工具秘密。排障顺序应为：确认状态 schema/reducer，确认边和路由返回值，确认最后消息类型与 tool_call ID，确认 context 到模型配置的映射，确认 checkpointer/thread_id，最后检查模型和外部工具。

交付时简洁说明改动的图结构、修改的文件、关键状态字段、运行/测试命令、用户必须配置的环境变量，以及尚未覆盖的风险或边界。对于部署到 LangGraph Server 的项目，检查 `langgraph.json` 图入口、依赖定义、环境变量注入与持久化服务连接；开发服务器和生产运行环境不得共享明文开发密钥。

## 代码审查清单

在完成 LangGraph 相关开发或评审时，逐项检查：状态字段有明确 reducer；节点只返回局部、可合并更新；所有路径从 `START` 可达并有终止条件；条件边映射完整；工具调用与 `ToolMessage.tool_call_id` 对齐；工具暴露最小权限；动态 `Command` 不与静态边产生意外双执行；中断不被捕获且恢复前副作用幂等；同一恢复流程使用正确 `thread_id`；子图 checkpointer 模式符合期望；并行路径没有无 reducer 的写入竞争；流式、超时、取消和错误都有可观察处理；测试涵盖主要路径和恢复路径。

如果任一关键点无法验证，不要声称实现已完成，应明确说明原因并提出下一步最小验证方案。
