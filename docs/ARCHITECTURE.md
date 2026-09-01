# CodePilot 架构

CodePilot 是多 Agent 动态工作流：前端聊天提出目标，LangGraph Platform 编排四个子图（研究 / 决策 / 生产 / 评审），状态走共享 State Bus，门禁与人工确认通过 Checkpoint 与 `interrupt` 落地。

## 1. 总体架构

```
┌─────────────────────┐     POST /threads/{id}/runs/stream     ┌──────────────────────────┐
│  Frontend (React)   │ ─────────────────────────────────────► │  LangGraph Platform      │
│  Vite + TS          │ ◄───────────────────────────────────── │  :2024  langgraph dev    │
│  映射 updates/SSE    │   updates / messages / interrupt       │                          │
└─────────────────────┘                                        │  graph: main_workflow    │
                                                               │    classify              │
                                                               │    research 子图         │
                                                               │    data 子图 + 锦标赛     │
                                                               │    produce 六步静态流     │
                                                               │    qa 评委 + 三道门       │
                                                               │    human_confirm         │
                                                               │                          │
                                                               │  State Bus + Checkpointer│
                                                               │  SQLite / PostgresSaver  │
                                                               └──────────────────────────┘
```

本地启动：`cd backend && make run`（`langgraph dev`），Studio / API 在 `http://127.0.0.1:2024`。图 id 为 `main_workflow`（见 `backend/langgraph.json`）。

## 2. 分层职责

| 层 | 位置 | 职责 | 禁止 |
|----|------|------|------|
| 前端 | `frontend/` | 创建 thread、订阅 runs/stream、渲染节点轨迹 | 直连 LLM、绕过门禁 |
| 主图 | `graphs/main_workflow.py` | 四闭环编排与 QA 后 rerun | 把子图逻辑摊进主图 |
| 子图 | `graphs/{problem_discovery,decision,production,review}.py` | 各自拥有的 State Bus 字段 | 跨子图偷偷改别人的台账 |
| Harness | `backend/agents/*.yaml` | 角色、工具、output_schema、state_fields | 一个 YAML 同时扮演生产与审核 |
| Skills | `backend/skills/` | 工具入口（search_km / query_sql / 截图 / 部署等） | 在节点里复制工具实现 |
| State Bus | `states/` | 事实/规则/问题台账、规格、产物、门禁 | `operator.add` 膨胀台账 |
| Checkpoint | `core/checkpointer.py` | 平台注入 / SqliteSaver / PostgresSaver | 给 Platform 再挂一份进程内 saver |

## 3. 关键技术决策

### 3.1 运行时：LangGraph Platform，而不是自建 FastAPI 聊天接口

主图以原生子图挂到 `main_workflow`，演示门 `interrupt` 后用 `Command(resume={'approved': true})` 恢复。前端默认 `VITE_API_BASE_URL=http://localhost:2024`，`POST /threads` 再 `POST /threads/{id}/runs/stream`，`assistant_id` 为 `main_workflow`。

### 3.2 State Bus 与 Prompt 裁剪

每个 Harness 声明 `state_fields`。节点通过 `slice_state` / `format_state_context` 只注入允许字段，避免把整份 facts/rules/issues dump 进每个 Agent。

### 3.3 结构化输出

`invoke_agent` 在 tool-call 循环结束后，用 Pydantic 按 YAML `output_schema` 校验；失败再试 `with_structured_output`。台账条目由 `FactEntryModel` / `IssueEntryModel` 构造。

### 3.4 记忆与 Checkpoint

- 分域 Agent 记忆 + `vector_memory`（持久化 JSON + embedding cosine）
- 占位 `DATABASE_URL` 不会当成活库；真实 Postgres 时用 PostgresSaver
- 本地默认 SqliteSaver 写到 `backend/checkpoints/main.sqlite`
- `langgraph.json` 导出的 `graph` **不**预挂进程内 checkpointer，交给平台注入

### 3.5 视觉对比

GENERATE 写出 `design.html`，BUILD 用 REPL 写出 `index.html`。COMPARE 对这两份 HTML 截图（Playwright，否则 DOM 光栅化），再 `screenshot_diff`。占位图标记 `mode: placeholder` 时视觉门不得通过。

## 4. 前端事件映射

LangGraph SSE 被映射为 UI 事件：

| 平台事件 | UI |
|----------|-----|
| `updates` 节点输出 | `tool_call` / `tool_result` |
| `messages` 文本 | `token` |
| `__interrupt__` | `token`（演示门提示） |
| `end` | `done`（携带 `thread_id`） |
| `error` | `error` |

演示门恢复：在 Studio 或 API 对同一 thread 发送 `Command(resume={'approved': true, 'comment': '...'})`。

## 5. 本地开发

1. `cd backend && make run` → `http://127.0.0.1:2024`
2. `cd frontend && npm run dev` → Vite 默认 5173，请求打到 2024
3. `cd backend && make test` / `make eval`
