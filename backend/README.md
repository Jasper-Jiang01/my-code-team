# CodePilot Backend

多 Agent 动态工作流系统的后端服务，基于 LangGraph + LangChain 构建。

## 项目结构

```
backend/
├── pyproject.toml              # Python 项目配置与依赖
├── Makefile                    # 常用命令快捷方式
├── .env.example                # 环境变量模板
├── .gitignore
├── .python-version             # Python 版本锁定
├── src/codepilot/              # 主包
│   ├── __init__.py
│   ├── py.typed
│   ├── core/                   # 核心配置与模型
│   │   ├── config.py           # Settings (Pydantic)
│   │   └── create_model.py     # ChatModel 工厂
│   ├── states/                 # 状态定义
│   │   └── workflow_state.py   # 全局 State Bus (TypedDict)
│   ├── nodes/                  # 节点函数
│   │   ├── classify_task.py    # 任务分类
│   │   ├── route_task.py       # 动态路由
│   │   ├── execute_research.py # 研究 Agent
│   │   ├── execute_data.py     # 数据 Agent
│   │   ├── execute_produce.py  # 生产 Agent
│   │   ├── execute_qa.py       # 质检 Agent
│   │   ├── execute_review.py   # 评审 Agent
│   │   ├── synthesize_results.py # 结果合成
│   │   └── human_confirm.py    # 人工确认
│   ├── graphs/                 # 图定义
│   │   ├── main_workflow.py    # 主工作流 (StateGraph)
│   │   ├── problem_discovery.py # 问题发现子图
│   │   ├── decision.py         # 决策子图
│   │   ├── production.py       # 生产子图
│   │   └── review.py           # 评审子图
│   └── tools/                  # 工具定义
│       ├── search_km.py        # KM 检索
│       ├── query_sql.py        # SQL 取数
│       ├── screenshot_diff.py  # 视觉比对
│       ├── deploy_demo.py      # Demo 部署
│       └── vector_memory.py    # 向量记忆
├── agents/                     # Agent Harness 配置
│   ├── research.yaml
│   ├── data.yaml
│   ├── design.yaml
│   ├── qa.yaml
│   └── review_panels/          # 五位评审视角
│       ├── platform.yaml
│       ├── assets.yaml
│       ├── user_poi.yaml
│       ├── merchant.yaml
│       └── city_supply.yaml
├── skills/                     # 可复用技能
│   ├── search_km.py
│   ├── query_sql.py
│   ├── screenshot_diff.py
│   └── deploy_demo.py
├── memory/                     # 记忆存储
│   ├── project_memory.json     # 项目级记忆
│   └── agent_memory/           # Agent 分域记忆
├── checkpoints/                # LangGraph Checkpoint 持久化
└── scripts/                    # 脚本工具
    └── setup.py
```

## 快速开始

```bash
# 安装依赖（含 langgraph-cli）
make install

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Keys

# 启动 LangGraph Platform 本地开发服务器（自带 Studio UI + API）
make run
# 等价于：langgraph dev
```

`langgraph dev` 会读取 `langgraph.json` 中注册的 `main_workflow` 图，启动本地调试服务器（默认 `http://127.0.0.1:2024`），并自动打开 LangGraph Studio 可视化调试界面，无需手写 FastAPI 服务即可通过 `/threads`、`/runs/stream` 等原生 API 调用工作流。

## 技术方案

详见上级目录的 [多Agent动态工作流系统技术方案.md](../多Agent动态工作流系统技术方案.md)。
