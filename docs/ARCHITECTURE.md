# CodePilot 技术方案

## 1. 系统定位

CodePilot 是一个 AI 编码副驾驶：用户在前端以聊天方式提出编码任务，后端 Agent 基于 LangGraph 进行多步推理（ReAct 循环），可调用工具完成代码检索、生成、检查等动作，并以 SSE 流式逐 token 返回。

## 2. 总体架构

```
┌─────────────────────┐         SSE (HTTP POST)        ┌──────────────────────────┐
│  Frontend (React)   │ ─────────────────────────────► │  Backend (FastAPI)       │
│  Vite + TS + SSE    │ ◄───────────────────────────── │  Controller (api/)       │
│  流式渲染聊天 UI     │   token / tool_call / done     │    │                     │
└─────────────────────┘                                │    ▼                     │
                                                       │  Agent (LangGraph)       │
                                                       │   agent ⇄ tools 循环     │
                                                       │   state: messages        │
                                                       │   checkpointer: memory   │
                                                       │    │                     │
                                                       │    ▼                     │
                                                       │  LLM Factory             │
                                                       │   ChatOpenAI / MockLLM   │
                                                       └──────────────────────────┘
```

### 分层职责

| 层 | 位置 | 职责 | 禁止 |
|----|------|------|------|
| Controller | `app/api/` | 解析请求、校验、调用 Agent、格式化 SSE | 业务逻辑、直接访问 LLM |
| Agent | `app/agents/` | 图编排、工具注册、状态管理 | HTTP 类型 |
| LLM | `app/llm/` | 模型实例化、降级策略 | 业务逻辑 |
| 横切 | config / errors / logging | 配置、错误、日志 | — |

## 3. 关键技术决策与依据

### 3.1 流式通道：SSE（而非 WebSocket / 轮询）

- Agent 响应是单向推送（服务端→客户端），SSE 恰好匹配；WebSocket 的双向能力、心跳、重连复杂度是过度设计
- 对话需要 POST body，故前端用 `fetch` + `ReadableStream` 手动解析 SSE 帧（与 OpenAI SDK 同款做法）
- 过代理/网关友好（纯 HTTP），后续要上 Nginx 无需额外协议升级

### 3.2 Agent 编排：LangGraph ReAct

- 裸 LangChain Chain 无法表达「调用工具 → 观察结果 → 再决策」的循环；LangGraph 将 Agent 建模为状态图，原生支持条件边、循环、checkpointer
- 状态 `AgentState.messages` 使用 `add_messages` reducer 累积对话，天然形成短期记忆
- Checkpointer MVP 用 `InMemorySaver`（单实例够用），生产替换 `PostgresSaver` / `RedisSaver` 仅需改工厂，路由/Agent 代码零改动——这是选 LangGraph 的核心收益

### 3.3 LLM 接入：工厂 + 降级

- `llm/factory.py` 统一实例化：配置了 `OPENAI_API_KEY` → `ChatOpenAI`（支持 `OPENAI_BASE_URL` 指向兼容网关/自部署模型）；未配置 → MockEchoLLM
- 降级保证：无 Key 环境（新同学 clone、CI）也能全链路跑通前端流式 UI，不被环境阻塞

### 3.4 类型契约：Pydantic 为源，手写 TS 镜像

- 前后端异构（TS/Python），tRPC 不适用；当前接口面小（chat + health），手写 `types/chat.ts` 与后端 `ChatRequest`/SSE 事件模型一一对应，成本最低
- 接口增长到 10+ 后，引入 `openapi-typescript` 从 FastAPI 自动生成的 openapi.json 生成 TS 类型，替换手写

### 3.5 后端框架：FastAPI

- LLM 调用是 IO 密集型，async/await 是刚需；FastAPI 原生 async + `StreamingResponse` 直接支撑 SSE
- Pydantic 内建请求校验（边界处信任为零），`pydantic-settings` 做集中配置 + 启动 fail-fast

### 3.6 前端：Vite + React 18 + TS（不用 Next.js）

- 纯客户端聊天应用，无 SEO/SSR 需求；Vite 冷启动与 HMR 快，构建产物静态化部署成本低
- 状态管理：MVP 用组件内 `useState` + 自定义 Hook；会话列表/多会话管理复杂化后再引入 Zustand

## 4. 横切关注点

- **配置**：全部环境变量，`Settings` 启动时校验；`.env.example` 入库，真实密钥永不入库
- **错误**：`AppError` 类型化体系 + 全局异常处理器统一 JSON 格式，含 request_id；程序性错误只记日志不泄栈
- **日志**：结构化 JSON，request-id 中间件生成并注入，所有日志可按请求串联
- **可观测**：`/health`（存活）+ `/ready`（就绪，报告 LLM 配置状态）；SSE `done` 帧携带会话元信息
- **安全**：CORS 显式白名单（dev 为 5173）；输入校验全量覆盖；密钥只走环境变量

## 5. SSE 事件协议（前后端契约）

```
event: token        data: {"content": "每"}
event: tool_call    data: {"name": "search_code", "args": {...}}
event: tool_result  data: {"name": "search_code", "result": "..."}
event: done         data: {"session_id": "..."}
event: error        data: {"code": "AGENT_ERROR", "message": "..."}
```

## 6. 演进路线

1. **MVP（当前）**：单会话 ReAct Agent + SSE 流式 + Mock 降级
2. **多会话**：`thread_id` 透传 + Postgres checkpointer + 会话列表接口
3. **真实工具**：接入文件系统读写、代码检索（ripgrep）、沙箱执行
4. **人审**（Human-in-the-loop）：LangGraph `interrupt` + 前端审批 UI
5. **生产化**：OpenAPI codegen 类型链路、Rate limit、结构化 tracing（LangSmith）
