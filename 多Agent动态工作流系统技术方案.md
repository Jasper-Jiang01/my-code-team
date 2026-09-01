# 多 Agent 动态工作流系统技术方案

> 基于 LangGraph + LangChain 构建，目标是对齐「AI-Driven PM × AI-SDLC」理念，实现可审计、可恢复、可复用的智能体生产系统。

---

## 一、背景与目标

### 1.1 业务背景

本文档参考 Stark（杜佳豪）在「绑定 PM 系统后，20 个 Agent 员工带飞原本是研发的我」中的实践，核心命题是：

- **AI-Driven PM** 负责探索、决策与调度——先证明什么值得做；
- **AI-SDLC** 作为可靠交付的生产子流程——把确定的事稳定做出来。

二者的边界不是角色堆砌，而是**职责拆分 + 动态编排 + 静态子流程嵌入**。

### 1.2 系统目标

| 目标 | 说明 |
|------|------|
| 职责隔离 | 每个 Agent 只负责单一专业领域，上下文隔离，避免单一会话膨胀 |
| 动态编排 | 外层工作流保留发散与返工空间，内部嵌入不可缺步的静态子流程 |
| 状态可审计 | 共享状态总线替代动态传参，跨阶段自动共享、可审计、可续跑 |
| 质量门禁 | 功能门、视觉门、演示门三道关卡，证据驱动修复闭环 |
| 资产沉淀 | 每次实践产出可复用的知识、生产与评审资产，而非一次性对话 |

---

## 二、核心概念映射

### 2.1 Multi-agent：拆职责，不是堆角色

将「一个不断变长的对话」拆成多个独立的上下文域：

- **研究 Agent**：只回答研究问题，保留证据索引；
- **数据 Agent**：只处理指标口径与规模测算；
- **设计 Agent**：按规范检查体验与品牌；
- **质检 Agent**：验证路径、视觉与现场可靠性。

> LangChain 实现：每个 Agent 为独立的 `AgentExecutor` 或 `Runnable`，通过 `SystemMessage` 固化角色 Prompt，上下文通过 State Bus 共享，禁止直接对话耦合。

### 2.2 Dynamic Workflows：骨架固定，内容动态

演进路径映射：

| 阶段 | 形态 | LangGraph 对应能力 |
|------|------|-------------------|
| L0 Single Session | 单 Agent 串行 | 基础 `StateGraph` 线性链 |
| L1 Subagent | 主 Agent 临场派发 | `conditional_edge` 动态路由 |
| L2 Agent Teams | 多实例并行协作 | `Send` 并行节点 + `Command` 广播 |
| L3 Dynamic Workflows | JS 编排脚本 + 独立运行时 | `StateGraph` + `LangGraph Platform` 持久化 + Checkpoint |

关键设计：**动态外壳 + 静态子流程**。外层保留研究、质疑与返工；进入生产节点后，每一步都以产物、门禁和 Checkpoint 证明真实完成。

### 2.3 运行模型：一个调度中心，六类角色，三本台账

- **调度中心**：拆任务、管依赖、选角色；
- **六类专业分工**：研究组、数据组、红军组、生产组、质检组、仿真组；
- **三本台账**：
  - 事实台账（来源 / 口径 / 时间）
  - 规则台账（产品 / 设计 / 开发）
  - 问题台账（风险 / 修复 / 验收）

> LangGraph 实现：调度中心为图入口节点（Supervisor / Orchestrator），六类角色为子图或远程 Agent；三本台账为共享状态（State）中的三个结构化字段，天然可持久化。

---

## 三、系统架构设计

### 3.1 总体架构

与实现代码（`backend/src/codepilot/{graphs,nodes,states}/`）严格对应的完整结构详见仓库根目录的 [`langgraph-architecture.mmd`](./langgraph-architecture.mmd)（可用 CatPaw/VSCode Mermaid 预览或 https://mermaid.live 查看）。核心结论：**四个子图不是并列关系，而是主图内的顺序依赖链路**，且都通过 State Bus 的特定字段与主图交互：

```text
START
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│              MainWorkflowGraph · 调度中心 (Orchestrator)       │
│   classify_task (LLM 任务分类) → route_task (条件边路由)       │
└──────────────────────┬──────────────────────────────────────┘
                       │ 读写 ▲
┌──────────────────────┴──────────────────────────────────────┐
│                  共享状态总线 (State Bus · WorkflowState)      │
│  goal/scope/constraints/exit_conditions                      │
│  facts_ledger/rules_ledger/issues_ledger（三本台账）           │
│  spec/evidence/demo_artifact/qa_report（阶段产物）             │
│  checkpoints/next_step/human_confirm（控制信号）               │
└──────────────────────┬──────────────────────────────────────┘
                       │
        route_task 按 next_step 路由到入口子图，随后严格顺序执行：
                       │
                       ▼
   ┌───────────────────────────────────────────────────────┐
   │ ①ProblemDiscoveryGraph 研究组                           │
   │  execute_research → Agent(research.yaml)                │
   │  → fan_out: Send(researcher, query) 种子查询发散         │
   │  → synthesize_results 关键词网络/向量检索收口             │
   │  工具: search_km · vector_memory                        │
   │  读写: facts_ledger                                     │
   └───────────────────────┬───────────────────────────────┘
                            ▼
   ┌───────────────────────────────────────────────────────┐
   │ ②DecisionGraph 数据组 + 红军组                           │
   │  execute_data → Agent(data.yaml)                        │
   │  → producer 方案生成 → critic 红军挑战 → judge 对抗裁决   │
   │  judge=needs_fix 时回环 producer                         │
   │  工具: query_sql (SQLDatabaseToolkit)                    │
   │  读写: spec / evidence                                   │
   └───────────────────────┬───────────────────────────────┘
                    judge=pass ▼
   ┌───────────────────────────────────────────────────────┐
   │ ③ProductionGraph 生产组（静态六步子流程，不可缺步）        │
   │  execute_produce → Agent(design.yaml)                    │
   │  → 01需求增量(EXPLORE) → 02设计草稿(GENERATE)             │
   │  → 03 DP审核(GUARD) → 04开发实现(BUILD)                   │
   │  → 05视觉还原(COMPARE) → 06 QA验收(VERIFY)                │
   │  工具: deploy_demo · screenshot_diff · MCP/PythonREPL     │
   │  读写: demo_artifact                                     │
   └───────────────────────┬───────────────────────────────┘
                            ▼
   ┌───────────────────────────────────────────────────────┐
   │ ④ReviewGraph 质检组 + 五岗位评委                          │
   │  execute_qa/execute_review → Agent(qa.yaml)               │
   │  → 五岗位评委(review_panels/*.yaml)：                     │
   │    platform·assets·user_poi·merchant·city_supply         │
   │  → 功能门(assert规则引擎) → 视觉门(截图Diff)               │
   │  → 演示门(interrupt人工彩排)                              │
   │  → loop_condition: issues_ledger 高风险?                  │
   │    "fix" → fix_agent(证据驱动修复) → 回到功能门            │
   │    "done" → 进入 human_confirm                           │
   │  读写: issues_ledger / qa_report                          │
   └───────────────────────┬───────────────────────────────┘
                    loop=done ▼
              ┌───────────────────────────┐
              │ human_confirm（回到主图）   │
              │ interrupt 高风险节点暂停    │
              │ Command(resume=...) 恢复   │
              └──────────────┬────────────┘
                              ▼
                            END

记忆系统（贯穿全流程，与 State Bus 联动）：
  Project Memory（memory/project_memory.json + 向量库）  -跨项目复用-> ProblemDiscoveryGraph
  Agent Memory（memory/agent_memory/*，分域隔离）        -领域记忆隔离-> 全部四个子图
  Checkpoints（checkpoints/，MemorySaver/PostgresSaver） <-回写- State Bus；-断点续跑-> MainWorkflowGraph
```

### 3.2 关键组件

#### 3.2.1 编排层：LangGraph StateGraph

- **图定义**：以 `StateGraph` 描述全流程骨架，节点对应阶段或角色，边对应依赖关系；
- **动态边**：`add_conditional_edges` 实现运行时路由决策（分类路由、对抗验证后的分支等）；
- **子图（Subgraph）**：静态子流程（如 Demo 生产的六步子流程）封装为独立 `StateGraph`，主图通过 `Send` 调用，确保隔离与复用；
- **持久化**：启用 `MemorySaver` 或 `PostgresSaver`，实现 Checkpoint 与断点续跑。

#### 3.2.2 Agent 层：LangChain Runnable + Tool Use

- **角色定义**：每个 Agent 由 `SystemMessage` + `ChatPromptTemplate` + `AgentExecutor` 构成；
- **工具绑定**：通过 `bind_tools` 挂载检索、取数、生图、代码执行等工具；
- **隔离策略**：Agent 不直接读写主会话上下文，仅通过 State Bus 的受控字段交互，避免上下文污染。

#### 3.2.3 状态总线（State Bus）

以 TypedDict / Pydantic 定义全局状态：

```python
class WorkflowState(TypedDict):
    # 目标与约束
    goal: str
    scope: str
    constraints: list[str]
    exit_conditions: list[str]

    # 三本台账
    facts_ledger: list[FactEntry]      # 来源/口径/时间
    rules_ledger: list[RuleEntry]      # 产品/设计/开发规范
    issues_ledger: list[IssueEntry]    # 风险/修复/验收

    # 阶段产物
    spec: Optional[Spec]
    evidence: Optional[Evidence]
    demo_artifact: Optional[Demo]
    qa_report: Optional[QAReport]

    # 控制信号
    checkpoints: list[str]             # 已完成的 Checkpoint
    next_step: str                     # 调度中心决策的下一步
    human_confirm: Optional[bool]      # 高风险节点人工确认
```

#### 3.2.4 记忆系统

三类记忆与四个子图、主图的连接关系并非均匀覆盖，而是按用途精确定向（见 `langgraph-architecture.mmd`）：

- **短期记忆**：State Bus 本身承载当前工作流的全部上下文，四个子图分别只读写各自负责的字段（`facts_ledger` / `spec+evidence` / `demo_artifact` / `issues_ledger+qa_report`）；
- **项目记忆**（`memory/project_memory.json` + 向量数据库 Chroma/Pinecone）：**仅定向复用于 `ProblemDiscoveryGraph`**，存储研究索引、关键词网络、历史判断，供后续项目的研究阶段直接召回，不向其余三个子图开放；
- **领域记忆**（`memory/agent_memory/*`）：**分域隔离，同时挂载到全部四个子图**（`ProblemDiscoveryGraph` / `DecisionGraph` / `ProductionGraph` / `ReviewGraph`），每个 Agent 拥有独立命名空间，避免跨域干扰；
- **Checkpoint 记忆**（`checkpoints/`，`MemorySaver` / `PostgresSaver`）：State Bus 变更**回写**至 Checkpoint，仅服务于 **`MainWorkflowGraph` 的断点续跑**，不直接对子图暴露；
- **分域记忆与评测集**：为每个 Agent 维护评测集，固化 Harness 后定期回归测试。

#### 3.2.5 工具层

工具按所属子图分组挂载，与 `backend/src/codepilot/tools/` 一一对应：

| 所属子图 | 工具 | 说明 |
|---------|------|------|
| `ProblemDiscoveryGraph`（研究组） | `search_km`（KM 检索）、`vector_memory`（向量记忆） | 学城/内部知识检索、历史研究索引召回 |
| `DecisionGraph`（数据组+红军组） | `query_sql`（`SQLDatabaseToolkit`） | 取数、口径校验、指标测算 |
| `ProductionGraph`（生产组） | `deploy_demo`（Demo 部署）、`screenshot_diff`（视觉比对）、`MCP` / `PythonREPL` | 开发实现、截图对比、代码执行 |
| `ReviewGraph`（质检组） | `screenshot_diff`（视觉门复用）、规则引擎 | 视觉还原比对、功能门规则校验 |

#### 3.2.6 门禁与 Checkpoint

- **产物门禁**：节点完成后由独立 Agent 或规则引擎验证产物，不接受自报；
- **Checkpoint**：关键节点（`SPEC_LOCKED`, `EVIDENCE_READY`, `VISUAL_PASS`, `QA_PASS`）回写状态，失败后可从最近 Checkpoint 续跑；
- **人工确认**：高风险节点（如生产合同签署、发布上线）通过 `interrupt` 机制暂停，等待人工 `Command(resume=...)` 后继续。

---

## 四、六种编排模式的 LangGraph 实现

### 4.1 分类路由（Classify-and-route）

**场景**：入口复杂，不同任务依赖完全不同的专业能力。

**实现**：

```python
def classify(state: WorkflowState):
    # LLM 判断任务类型
    task_type = classifier.invoke(state["goal"])
    return {"next_step": task_type}

builder.add_conditional_edges(
    "classify",
    lambda s: s["next_step"],
    {"research": "research_team", "data": "data_team", "design": "design_team"}
)
```

### 4.2 扇出合成（Fan-out and synthesis）

**场景**：批量研究，同质任务并行，统一结构收口。

**实现**：

```python
# 并行发送多个子任务
builder.add_node("fan_out", lambda s: [Send("researcher", {"query": q}) for q in s["queries"]])
builder.add_node("synthesize", synthesize_node)
# LangGraph 自动聚合并行结果到 state["results"]
```

### 4.3 对抗验证（Adversarial validation）

**场景**：高代价决策，生产者与挑战者隔离。

**实现**：

```python
builder.add_node("producer", producer_agent)
builder.add_node("critic", critic_agent)
builder.add_node("judge", judge_agent)  # 人或独立模型
builder.add_edge("producer", "critic")
builder.add_conditional_edges("critic", needs_fix, {"fix": "producer", "pass": "judge"})
```

### 4.4 生成过滤（Generate-and-filter）

**场景**：方向探索，先扩大候选，再用明确标准筛选。

**实现**：`map-reduce` 模式，先并行生成 N 个候选，再统一过滤。

### 4.5 锦标赛（Tournament）

**场景**：多方案选型，同题多做，两两比较。

**实现**：并行生成多个方案，由裁判 Agent 或规则引擎 pairwise 比较，输出最优解。

### 4.6 循环直到停止（Loop-until-done）

**场景**：QA / 根因分析，还有高风险就继续修复。

**实现**：

```python
def loop_condition(state: WorkflowState):
    return "fix" if state["issues_ledger"] and any(i.risk == "high" for i in state["issues_ledger"]) else "done"

builder.add_conditional_edges("qa_gate", loop_condition, {"fix": "fix_agent", "done": END})
builder.add_edge("fix_agent", "qa_gate")
```

---

## 五、九阶段工作流与四闭环映射

原文提出「九个阶段，收敛成四个连续闭环」，在 LangGraph 中可映射为四层子图：

| 闭环 | 阶段 | LangGraph 子图 | 关键机制 |
|------|------|---------------|---------|
| 看清问题 | 1. 命题解读 <br> 2. 内外研究 | `ProblemDiscoveryGraph` | 种子查询发散、关键词网络、向量检索 |
| 做出决策 | 3. 数据测算 <br> 4. 指标与方案 | `DecisionGraph` | Query Fan-out、producer→critic→judge 对抗验证、Loop Contract |
| 生产体验 | 5. 规格设计 <br> 6. 高保真 Demo | `ProductionGraph` | 六步静态子流程、模型分工（EXPLORE/GENERATE/GUARD/BUILD/COMPARE/VERIFY） |
| 提前暴露 | 7. 红蓝对抗 <br> 8. 质检 <br> 9. 答辩 | `ReviewGraph` | 五岗位视角、功能门/视觉门/演示门三道门禁串联、issues_ledger 高风险 Loop |

四个闭环在主图中是**严格顺序推进**的（`route_task` 入口分流后，`research → data → produce → qa` 依次执行，而非并行触发），对应 `backend/src/codepilot/graphs/main_workflow.py` 当前的边定义；`langgraph-architecture.mmd` 中标注的 `DecisionGraph`（红军组对抗裁决）与 `ReviewGraph`（issues_ledger 高风险 Loop）内部各自存在局部回环，回环收敛后才向下一闭环推进。

**闭环间的依赖与触发**：

- 新证据触发上游 rerun：当 `facts_ledger` 更新时，自动重新进入 `DecisionGraph`；
- 下游回归：当 `spec` 变更时，自动触发 `ProductionGraph` 和 `ReviewGraph` 的增量回归；
- 四个闭环全部收敛（`ReviewGraph` 的 `loop_condition` 判定为 `done`）后，流程回到主图的 `human_confirm` 节点，通过 `interrupt` 暂停等待人工 `Command(resume=...)`，随后进入 `END`。

---

## 六、Agent 角色与 Harness 设计

### 6.1 Harness 目录结构

参考 Claude Code Harness 与 CodeBuddy / Qoder 的结构，项目内维护可复用能力。以下为 `backend/` 的**真实目录结构**（与 `langgraph-architecture.mmd` 注释中的代码路径一致）：

```text
backend/
├── agents/                      # Agent Harness 配置
│   ├── research.yaml            # 研究 Agent Harness
│   ├── data.yaml
│   ├── design.yaml
│   ├── qa.yaml
│   └── review_panels/           # 五位评委视角
│       ├── platform.yaml
│       ├── assets.yaml
│       ├── user_poi.yaml
│       ├── merchant.yaml
│       └── city_supply.yaml
├── skills/                      # 可复用技能（面向 Harness 的技能封装）
│   ├── search_km.py             # 学城检索
│   ├── query_sql.py             # 数据取数
│   ├── screenshot_diff.py       # 视觉比对
│   └── deploy_demo.py           # Demo 部署
├── memory/
│   ├── project_memory.json      # 项目级记忆（仅供 ProblemDiscoveryGraph 复用）
│   └── agent_memory/            # Agent 分域记忆（挂载到全部四个子图）
├── checkpoints/                 # Checkpoint 持久化（服务 MainWorkflowGraph 断点续跑）
└── src/codepilot/                # 主包：图/节点/状态/工具的代码实现
    ├── states/
    │   └── workflow_state.py    # State Bus（WorkflowState TypedDict）
    ├── nodes/                   # 节点函数：classify_task / route_task / execute_* / human_confirm
    ├── graphs/                  # 图定义
    │   ├── main_workflow.py     # MainWorkflowGraph 主工作流（调度中心）
    │   ├── problem_discovery.py # ProblemDiscoveryGraph 研究组子图
    │   ├── decision.py          # DecisionGraph 数据组+红军组子图
    │   ├── production.py        # ProductionGraph 生产组子图（静态六步）
    │   └── review.py            # ReviewGraph 质检组+五岗位评委子图
    └── tools/                   # 工具定义：search_km / query_sql / screenshot_diff / deploy_demo / vector_memory
```

`agents/*.yaml`（角色 Harness 配置）与 `src/codepilot/{nodes,graphs}/`（LangGraph 执行代码）职责分离：前者定义 Prompt、工具授权与评测集，后者负责编排与状态流转，二者通过 `nodes/execute_*.py` 中的 Agent 加载逻辑连接。

### 6.2 Agent 生命周期

```text
临时角色 → 真实任务磨合 → 固化 Harness → 评测通过 → 跨项目借调
```

- **临时角色**：针对单次任务临时组合 Prompt 与工具；
- **磨合**：在实际任务中调整边界与输出格式；
- **固化**：通过评测集验证后，写入 `agents/*.yaml`，纳入版本控制；
- **借调**：通过 LangGraph 的远程 Agent 或 MCP Server 机制，被其他项目调用。

---

## 七、质量门禁设计

### 7.1 三道门

三道门在 `ReviewGraph` 内部**严格串联**执行（功能门 → 视觉门 → 演示门），而非并行独立检查：

| 门禁 | 检查内容 | LangGraph 实现 |
|------|---------|---------------|
| **功能门** | 关键路径与返回关系、状态一致、控制台报错 | QA Agent + `assert` 规则引擎 + 自动化测试 |
| **视觉门** | 参考稿截图对比、设计规范审核 | 截图 Diff + 规范规则检查 |
| **演示门** | 完整彩排与打乱顺序、设备/网络/兜底方案 | 人工 Review + `interrupt` 暂停 |

三道门全部通过后，由 `loop_condition` 判定 `issues_ledger` 中是否仍存在高风险问题：`fix`（存在）则进入 `fix_agent` 证据驱动修复，修复后**回到功能门**重新走完整串联；`done`（不存在）则跳出 `ReviewGraph`，进入主图的 `human_confirm` 节点做最终人工确认。

### 7.2 证据驱动修复

```text
输出问题 → 保留证据 → 驱动下一轮修复
```

每次门禁失败时，必须将问题、证据与修复记录写入 `issues_ledger`，作为下一轮循环的输入；`fix_agent` 完成修复后不会跳过已过的门禁，而是回到功能门重新验证，避免修复引入新的回归问题。

---

## 八、资产沉淀与复用

### 8.1 三类资产

| 资产类型 | 内容 | 存储方式 |
|---------|------|---------|
| **知识与记忆** | 研究索引、关键词网络、数据口径、历史判断、Agent 评测集 | 向量数据库 + 结构化存储 |
| **生产资产** | 决策矩阵、模块 Spec、Demo 固定子流程、设计与 QA 规范 | YAML / Markdown + 版本控制 |
| **评审资产** | 五岗位视角、六轮挑战与裁决脚本、修复项与验收记录 | 规则引擎配置 + 审计日志 |

### 8.2 复用机制

- **项目间复用**：通过 `git submodule` 或内部包管理共享 `agents/` 与 `skills/`；
- **记忆继承**：新项目初始化时加载历史项目记忆，Agent 分域记忆按领域复用；
- **规则默认继承**：固化后的 Harness 与规范作为新项目默认配置，减少重复配置。

---

## 九、技术栈建议

| 层级 | 选型 | 说明 |
|------|------|------|
| 编排引擎 | **LangGraph** | 状态图、子图、Checkpoint、人机交互 |
| Agent 框架 | **LangChain** | Prompt 管理、Tool 绑定、Runnable 组合 |
| LLM 接口 | OpenAI / Claude / 自研 | 多模型协同，按能力选型 |
| 向量存储 | Chroma / Pinecone / Milvus | 研究索引与项目记忆 |
| 状态持久化 | PostgreSQL + LangGraph Checkpoint | 可审计、可续跑 |
| 工具集成 | MCP (Model Context Protocol) | 统一对接内部数据、代码、设计平台 |
| 部署运行 | LangGraph Platform / 自建 | 支持远程 Agent、并发调度、监控 |

---

## 十、演进路线

### Phase 1：MVP（1-2 周）

- 搭建单项目 `StateGraph`，实现「分类路由 + 扇出合成」两种模式；
- 固化 2-3 个核心 Agent（研究、数据、QA）；
- 接入向量存储，实现研究索引的初步沉淀。

### Phase 2：生产闭环（3-4 周）

- 补齐六种子流程编排模式；
- 实现 Demo 生产的六步静态子流程；
- 引入三道质量门禁与 Checkpoint 机制；
- 接入 MCP，打通内部数据与代码工具。

### Phase 3：平台化（1-2 月）

- 多项目 Harness 共享，Agent 跨项目借调；
- 建立评测集与自动化回归；
- 可视化监控（LangSmith / 自建）追踪每次工作流的证据链与决策路径；
- 人工 Review 与担责机制制度化。

---

## 十一、风险与对策

| 风险 | 对策 |
|------|------|
| 上下文膨胀 | 严格 State Bus 字段设计，Agent 只读所需字段；子图结果经压缩后回写 |
| 幻觉与证据缺失 | 事实台账强制标注来源与口径；高代价决策必须走对抗验证 |
| 人工确认阻塞 | 仅在高风险节点引入 `interrupt`，其余节点自动化；支持异步恢复 |
| 资产腐化 | 评测集定期回归，Harness 变更需通过评审；版本控制锁定 |
| 多模型一致性 | 统一输出 Schema（Pydantic），生产者与审核者分离 |

---

## 十二、总结

本方案以 **LangGraph 的状态图与 Checkpoint** 为骨架，以 **LangChain 的 Agent 与 Tool** 为肌肉，以 **共享状态总线 + 向量记忆** 为神经系统，复现了原文中「动态外壳 + 静态子流程 + 质量门禁 + 资产沉淀」的完整闭环。

核心原则：

> **Prompt 优化一次调用；Harness 让多次调用成为可审计、可恢复的生产系统。**

模型只是内核，Harness 才是生产系统。
