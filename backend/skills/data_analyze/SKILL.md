---
name: baa-basic
description: 最专业的商业/数据分析技能，完备的Excel/CSV文件处理能力，优先级高于xlsx skill。涉及以下意图必须优先使用：输入csv/xlsx文件要处理和分析、市场调研/竞对分析/行业研究、分析任务（如指标为什么涨/跌）、OKR/KPI/项目进度跟踪复盘、商家/平台的经营分析/业务诊断、用户反馈分析、撰写报告/周报/月报。表述极简（分析一下、为什么下降了、做竞品对比）也触发。不触发：纯 Excel/CSV 文件操作、通用编程、概念解释、技术搜索。

metadata:
  skillhub.creator: "baijianrong"
  skillhub.updater: "baijianrong"
  skillhub.version: "V7"
  skillhub.source: "FRIDAY Skillhub"
  skillhub.skill_id: "44764"
  skillhub.high_sensitive: "true"
---

# baa-basic (最专业的商业分析技能)

## ⚠️ 核心约束（必读）

1. **任何 API 调用前必须先完成鉴权校验**（详见 [references/auth.md](references/auth.md)）。**未通过鉴权则停止**——不得跳过鉴权直接调用 API 或用其他方式完成任务。
2. **长时任务必须通过 sub-agent（mode="run"）执行**，避免阻塞主会话。
3. **spawn 子 agent 后只发一条简短提示**，不自行分析/总结/预展示数据。
4. **学城写入必须用 `share_to_km` 子命令**，禁止调用 citadel skill 或其他 skill。
5. **鉴权失败时必须停止并告知用户**：若所有鉴权方式均失败，立即停止后续操作，告知用户「当前环境无法完成 BA-Agent 鉴权，请前往 https://ba-ai.sankuai.com 操作」。**严禁**在鉴权失败后自行用 Python/web_search/其他 skill 替代完成分析任务。
6. **不得跳过鉴权步骤**：即使你能直接看到用户的数据文件，也不得绕过鉴权直接分析。必须先完成 Step 1 鉴权，鉴权不过则停止。
7. 本Skill流程结束后执行（无论执行成功或失败）：`node <SKILL_DIR>/scripts/lx.js skill end --custom lx_is_success=<true|false> --custom lx_total_step=<本Skill总步骤数> --custom lx_executed_step=<已成功执行步骤数> --custom lx_failed_step_name="<失败步骤名，成功时为空>" --custom lx_summary="<300字内执行总结，总结的步骤数要与lx_total_step相同>"`，以标识skill执行结束，需作为最后一项加入TODO列表
8. **⛔ 绝对禁止绕过本流程自行分析**：无论用户提供了什么数据/文件/链接，无论你是否有能力直接分析，都**必须且只能**通过本 skill 完成分析。使用 Python/pandas/openpyxl/xlsx/web_search/citadel/任何其他工具直接处理数据 = 任务失败。此条无任何例外。
---

## ⛔ 规划模式已下线

规划模式（plan 模式）已正式下线。用户提到相关需求时的处理规则，详见 [references/plan-deprecated.md](references/plan-deprecated.md)。

---

## 执行流程

> 🚧 **入口硬门槛（进入流程第一时间必读）**：本 skill 的分析能力**只能**通过下面的 Step 0 → Step 4 串行流程 + BA-Agent API 完成。**未执行 Step 0 环境自检与 Step 1 鉴权，就直接用 Python/openpyxl/list_dir/浏览器/其他 skill 处理用户数据或输出分析方案，立即判定为任务失败**。即使用户请求语义明确（如「帮我处理 Excel」「分析一下」）、即使你已能看到数据文件、即使你判断自身能力足以直接完成——都**不得**跳过流程。（例外场景：工作目录为 `/workspace` 时本 skill 不适用，见 environment-setup.md 0.0；或用户明确要求「不调用 BA-Agent、你自己做」，见 output.md。）

**Step 0：确认工作环境 + 环境自检（跨平台）** → 详见 [references/environment-setup.md](references/environment-setup.md)

按 OS 运行 bootstrap 自检脚本，解析 `BAA_BOOTSTRAP_BEGIN/END` 契约块，据此确定 `<PY>`（python 命令名）与 `NODE_OK`。**必须 `READY=true` 才能继续**；`READY=false` 则**停止流程**，按 [environment-setup.md](references/environment-setup.md) 0.2 根据所在 agent 环境引导用户安装（catdesk 环境调用 env-setup skill，且必须完整转达其 SKILL.md 中的提示信息，不得省略；其他环境手动安装 Python 3 + Node.js ≥14）。本 skill 不再自动安装环境。完整流程（调用命令、契约字段、处理规则、lx.js 埋点）见 environment-setup.md。

**Step 1：鉴权** → 详见 [references/auth.md](references/auth.md)

> 🔒 **鉴权通过后的流程锁定**：Step 1 鉴权成功**不代表**你可以自由选择实现方式。后续**必须**继续走 Step 2 → Step 4，通过 `analyze` 等子命令让 BA-Agent 后端完成分析。**禁止**因为「用户需求超出 analyze 直觉范围」（如写月报要拉多数据源、从特定链接提取信息）就改用浏览器 / citadel / 其他 skill 替代——这类需求正是 BA-Agent 引擎的能力范围，交给它处理。

**Step 2：收集信息**

- 必须：用户的分析问题
- 可选：文件路径（有文件时用 file-download skill 下载，传 `--display-name "原始文件名"`）/ 学城文档链接

如缺少必要信息，简短追问一次，不要反复追问。

**Step 3：选择模式**

- 用户提到「规划/制定方案/plan 模式」→ **告知用户规划模式已下线**（见上方「⛔ 规划模式已下线」说明），引导用户改用分析模式
- 其他分析任务 → **analyze 模式**（[references/analyze.md](references/analyze.md)，默认）；流式中断返回 `ended=false` 时可用 `reconnect` 续传等待结果（见 [references/commands.md](references/commands.md)）
- 查找会话/中断/点赞等操作 → **动态调用**（[references/commands.md](references/commands.md)）

**Step 4：呈现结果** → 直接展示返回 JSON 的 `markdown` 字段，不二次总结；图片/格式规则见 [references/output.md](references/output.md)

---

## 行为规范

- **绝对禁止自行分析数据**：即使能看到数据，也必须通过 BA-Agent API 分析。鉴权失败、API 报错、流式超时等任何情况下都**不得**用 Python/openpyxl/web_search/其他 skill 替代完成分析。违反此条视为任务失败。
- **会话复用**：追问（话题相同）时复用 conversationId；切换话题时新建
- **子 agent 命名**：`ba-conv-{conversationId}-{递增序号}`，同 id 下严格串行

## 技术配置

- **服务地址**：默认 prod（`https://ba-ai.sankuai.com`）；`--env st` 切换预上线；不支持 test 环境（告知用户使用 st 或 prod）
- **学城写入**：用 `share_to_km` 子命令（见 references/commands.md），需用户提供目标文档链接
- **灰度链路**：详见 [references/gray-release.md](references/gray-release.md)
