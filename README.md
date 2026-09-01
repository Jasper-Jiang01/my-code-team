# CodePilot

多 Agent 动态工作流系统 —— 基于 LangGraph + LangChain 构建的可审计、可恢复、可复用的智能体生产系统。

## 目录结构

```
CodePilot/
├── README.md                                    # 本文件
├── 多Agent动态工作流系统技术方案.md              # 系统技术方案文档
└── backend/                                     # 后端服务
    ├── pyproject.toml                           # Python 项目配置
    ├── Makefile                                 # 快捷命令
    ├── .env.example                             # 环境变量模板
    ├── .gitignore
    ├── .python-version
    ├── README.md                                # 后端说明
    ├── src/codepilot/                           # 主包
    │   ├── __init__.py
    │   ├── py.typed
    │   ├── core/                                # 核心配置与模型
    │   │   ├── config.py
    │   │   └── create_model.py
    │   ├── states/                              # 状态定义
    │   │   └── workflow_state.py                # 全局 State Bus
    │   ├── nodes/                               # 节点函数
    │   │   ├── classify_task.py                 # 任务分类
    │   │   ├── route_task.py                    # 动态路由
    │   │   ├── execute_research.py              # 研究 Agent
    │   │   ├── execute_data.py                  # 数据 Agent
    │   │   ├── execute_produce.py               # 生产 Agent
    │   │   ├── execute_qa.py                    # 质检 Agent
    │   │   ├── execute_review.py                # 评审 Agent
    │   │   ├── synthesize_results.py            # 结果合成
    │   │   └── human_confirm.py                 # 人工确认
    │   ├── graphs/                              # 图定义
    │   │   ├── main_workflow.py                 # 主工作流
    │   │   ├── problem_discovery.py             # 问题发现子图
    │   │   ├── decision.py                      # 决策子图
    │   │   ├── production.py                    # 生产子图
    │   │   └── review.py                        # 评审子图
    │   └── tools/                               # 工具定义
    │       ├── search_km.py                     # KM 检索
    │       ├── query_sql.py                     # SQL 取数
    │       ├── screenshot_diff.py               # 视觉比对
    │       ├── deploy_demo.py                   # Demo 部署
    │       └── vector_memory.py                 # 向量记忆
    ├── agents/                                  # Agent Harness
    │   ├── research.yaml
    │   ├── data.yaml
    │   ├── design.yaml
    │   ├── qa.yaml
    │   └── review_panels/                       # 五位评审视角
    │       ├── platform.yaml
    │       ├── assets.yaml
    │       ├── user_poi.yaml
    │       ├── merchant.yaml
    │       └── city_supply.yaml
    ├── skills/                                  # 可复用技能
    │   ├── search_km.py
    │   ├── query_sql.py
    │   ├── screenshot_diff.py
    │   └── deploy_demo.py
    ├── memory/                                  # 记忆存储
    │   ├── project_memory.json
    │   └── agent_memory/
    ├── checkpoints/                             # Checkpoint 持久化
    └── scripts/                                 # 脚本工具
        └── setup.py
```

## 核心设计

- **编排引擎**：LangGraph `StateGraph` + 子图 + Checkpoint
- **Agent 框架**：LangChain `Runnable` + `bind_tools`
- **状态总线**：共享 `WorkflowState`（TypedDict），替代动态传参
- **质量门禁**：功能门、视觉门、演示门
- **资产沉淀**：知识记忆、生产资产、评审资产

## 快速开始

```bash
cd backend
cp .env.example .env
# 编辑 .env 填入 API Keys
make install
make run   # 等价于 langgraph dev，启动本地 LangGraph Platform 服务 + Studio UI
```

## 文档

- [多Agent动态工作流系统技术方案.md](./多Agent动态工作流系统技术方案.md)
- [backend/README.md](./backend/README.md)
