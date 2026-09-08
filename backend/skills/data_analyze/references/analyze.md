# 分析模式（analyze）详细规则

## 会话复用判断

**复用已有会话（传 `--conversation-id`）**，满足以下条件时：
- 用户的问题是对上一轮分析的追问、深挖、补充（如「再分析一下 B 渠道」、「刚才那个图能换成折线图吗」）
- 话题没有发生明显切换（仍围绕同一批数据或同一分析目标）
- 分析模式未变（仍是 analyze）

**新建会话**，满足以下条件时：
- 用户提出了全新的分析问题，与上一轮无关
- 用户明确说「重新分析」、「开个新会话」
- 上下文中没有 conversationId（第一次分析）

> 📌 analyze 是当前唯一可用的模式（plan 已下线），不存在"模式切换"场景，无需按模式判断是否新建会话。

## 项目模式前置流程

当用户提到「在某个项目下分析」、「用项目XXX的数据」、「基于项目提问」时，**主 agent 必须先在本地完成以下步骤，不得跳过**：

### Step A：获取项目详情

```bash
python3 {cwd}/scripts/call_ba_agent.py project_info <projectId>
```

返回结构：
```json
{
  "files": [],
  "sourceList": [],
  "ctx": {}
}
```

- **找不到项目**：告知用户「未找到该项目」，调 `projects` 列出所有项目供用户选择
- **找到项目**：记录 `files`、`sourceList`、`ctx`，进入 Step B

### Step B：沧澜数据集处理

**如果 `sourceList` 非空**，记录该列表，后续在 Step D 中通过 `--canglan-source-json` 直接传入 analyze 命令。新流程下无需单独调用 `fetch_data` 取数，后端会在 streamV2 执行时自动取数。

**如果 `sourceList` 为空**：跳过此步，直接进入 Step C。

### Step C：创建会话（主 agent 执行）

```bash
python3 {cwd}/scripts/call_ba_agent.py create_conv \
  --name "分析问题前50字" \
  --mode analyze \
  --project-id <projectId> \
  --ctx-json '<ctx 的 JSON 字符串>'
```

> 🚨 **必须传 `--ctx-json`（项目的 ctx）**，不得使用默认的空 ctx；`--project-id` 也必须同时传入。

### Step D：spawn 子 agent 执行分析

将 `sourceList`（JSON 字符串）通过 `--canglan-source-json` 传给子 agent：

```python
sessions_spawn(
  label="ba-conv-{conversationId}-1",
  mode="run",
  runTimeoutSeconds=1800,
  task="""你是 BA-Agent 执行专员。执行以下步骤，不读 SKILL.md，不做额外分析，不重鉴权：

Step 1：用 exec 执行分析命令：
  python3 {cwd}/scripts/call_ba_agent.py analyze '用户问题' \
    --conversation-id {conversationId} \
    --project-id {projectId} \
    --canglan-source-json '{sourceList的JSON字符串}' \
    {deep_agent_flag} --no-reauth

Step 2：解析返回 JSON，提取 chatUrl 和 markdown 字段（必须来自实际返回，不得伪造）。

Step 3：用 message tool 一次性发送完整图文内容：
  message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}", channel=daxiang, target={daxiang_user_id})
  **直接使用 markdown 原文，不做任何修改，图片语法 ![名称](url) 保持原样**

Step 4：发送完毕后，**仅将约定好的固定短语回复给主 agent**：
  - 成功：「✅ 已完成分析并推送大象消息」
  - 失败：「❌ 执行失败：{错误原文}」"""
)
```

> 如果 `sourceList` 为空（无沧澜数据集），省略 `--canglan-source-json` 参数。

---

## deepAgent 开关

用户可主动要求打开 **deepAgent 开关**（如说"用 deepAgent 分析"、"开启 deepAgent"、"deep agent 模式"、"深度分析"、"技能分析"）

开启方式：在创建会话时加 `--deep-agent` 参数（对应接口入参 `skillEnabled=1`）。

- 仅在**新建会话**时生效，复用会话（`--conversation-id`）时忽略此参数
- 默认不开启（`skillEnabled=0`）

命令示例：
```bash
# 新建会话时开启 deepAgent
python3 {cwd}/scripts/call_ba_agent.py create_conv --name "分析问题前50字" --mode analyze --deep-agent

# analyze/plan 子命令直接加 --deep-agent（新建会话时自动生效）
python3 {cwd}/scripts/call_ba_agent.py analyze "用户的问题" --deep-agent
```

---

## 非项目模式（无 project_id）

直接进入子 agent 执行，流程不变：

```bash
# Step A：主 agent 创建会话（如用户开启 deepAgent，加 --deep-agent）
python3 {cwd}/scripts/call_ba_agent.py create_conv --name "分析问题前50字" --mode analyze [--deep-agent]
```

```python
# Step B：spawn 子 agent
sessions_spawn(
  label="ba-conv-{conversationId}-1",
  mode="run",
  runTimeoutSeconds=1800,
  task="""你是 BA-Agent 执行专员。执行以下步骤，不读 SKILL.md，不做额外分析，不重鉴权：

Step 1：用 exec 执行分析命令：
  python3 {cwd}/scripts/call_ba_agent.py analyze '用户问题' \
    --conversation-id {conversationId} [--file ... --display-name ...] [--km-url ...] [--deep-agent] --no-reauth

Step 2：解析返回 JSON，提取 chatUrl 和 markdown 字段（必须来自实际返回，不得伪造）。

Step 3：用 message tool 一次性发送完整图文内容：
  message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}", channel=daxiang, target={daxiang_user_id})
  **直接使用 markdown 原文，不做任何修改，图片语法 ![名称](url) 保持原样**

Step 4：发送完毕后，**仅将约定好的固定短语回复给主 agent**：
  - 成功：「✅ 已完成分析并推送大象消息」
  - 失败：「❌ 执行失败：{错误原文}」"""
)
# Step C：spawn 后主 agent 只发送一条「正在分析中，请稍候…」提示，之后不再等待或回复
```

---

## 命令示例

```bash
# 新建会话（无文件）
python3 {cwd}/scripts/call_ba_agent.py analyze "用户的分析问题"

# 新建会话（带本地文件，必须同时传 --display-name 为用户的原始文件名）
python3 {cwd}/scripts/call_ba_agent.py analyze "用户的分析问题" \
  --file /tmp/files/1773834641.xlsx --display-name "原始文件名.xlsx"

# 新建会话（带学城文档）
python3 {cwd}/scripts/call_ba_agent.py analyze "用户的分析问题" \
  --km-url "https://km.sankuai.com/collabpage/xxx"

# 新建会话（带项目+沧澜数据集，--canglan-source-json 传入 sourceList 元信息，后端自动取数）
python3 {cwd}/scripts/call_ba_agent.py analyze "用户的分析问题" \
  --conversation-id <conversationId> \
  --project-id 12345 \
  --canglan-source-json '[{"id":"4392","type":"canglan-dataset","datasetName":"站内热搜词","businessLineId":"-99999","subscription":{"name":"站内热搜词分析","id":7381,"version":3}}]'

# 复用已有会话追问
python3 {cwd}/scripts/call_ba_agent.py analyze "用户的追问" --conversation-id <conversationId>
```

## 文件附件处理

当用户通过大象发送了文件时：
1. 从文件 URL 中提取 `filename=` 参数得到原始文件名（URL decode 后）
2. 按 file-download skill 规范下载到 `/tmp/files/{timestamp}.{ext}`
3. 调用脚本时附加 `--display-name "原始文件名"`

## 返回格式

```json
{
  "conversationId": "会话ID（后续操作需要）",
  "chatUrl": "https://ba-ai.sankuai.com/chat/xxxxx",
  "markdown": "分析结果Markdown文本",
  "ended": true,
  "chatResponseId": "流式消息块ID（ended=false 时用于重连，无则缺省）"
}
```

> `ended=false` 表示流式未正常结束（中途断开、内置自动重连未完成）。此时可用返回的 `chatResponseId` 调 `reconnect` 子命令续传等待完整结果（见下方「流式断开续传」）；若返回里没有 `chatResponseId`（一条消息都没收到就断），可直接调 `reconnect <conversationId>` 不带该参数，脚本自动从历史消息兜底。

## 流式断开续传（reconnect）

当 analyze 返回 `ended=false` 时，可使用 `reconnect` 子命令继续等待 BA-Agent 产出完整结论：

```bash
python3 {cwd}/scripts/call_ba_agent.py reconnect <conversationId> <chatResponseId> --no-reauth
```

- reconnect 内部会再次走 reconnectV2 重连循环（默认 5 次，可 `--max-reconnect` 调整）。
- 若 analyze 未返回 `chatResponseId`，可不传该参数，脚本自动从 historyListV2 取最新 server 消息 id 作为续传起点。
- 若重连后仍无完整结论，脚本自动从 historyListV2 拉取最新一轮 server 消息补全 summary。
- 返回结构与 analyze 一致（含 `ended`/`chatResponseId`），若仍 `ended=false` 可再次调用。
- **由 Agent 自行判断是否调用**：拿到 analyze 结果后，若 `ended=false` 且需要完整结论，再调用本命令；详见 [commands.md](commands.md)。

## 子 agent 执行规则

**分析任务必须通过 sub-agent（mode="run"）执行**，避免阻塞主会话。

### 子 agent 命名规则

每次针对某个 conversationId 发起新的长时操作，spawn 一个新的 `mode="run"` 子 agent，label 使用递增序号后缀：

```
ba-conv-{conversationId}-1   ← 第1次操作（首次 analyze）
ba-conv-{conversationId}-2   ← 第2次操作（追问）
ba-conv-{conversationId}-3   ← 第3次操作（再次追问）
```

**约束：** 同一 conversationId 下序号严格递增，不同 conversationId 之间序号独立。

### 串行执行原则

🚨 **子 agent 之间严格串行，不得并发**。必须等前一个子 agent 完成后，才能 spawn 下一个。

### ⚠️ 主 agent 回复时机（关键）

**所有场景（首次分析 & 追问）下，子 agent 都必须自己直接推大象消息，不把分析内容回给主 agent。**

- spawn 后主 agent 只发送一条「正在分析中，请稍候…」的提示，之后**不再等待、不再呈现任何分析内容**
- 若子 agent 报错，子 agent 将错误信息直接推送给用户，主 agent 无需额外处理

**子 agent task 中需替换的变量（spawn 时由主 agent 填入）：**
- `{conversationId}`：当前会话 ID
- `{projectId}`：项目 ID（项目模式时）
- `{sourceList的JSON字符串}`：project_info 返回的 sourceList JSON（项目+沧澜模式时）
- `{daxiang_user_id}`：用户的大象 ID（从 inbound_meta 的 chat_id 取，格式为 `user:XXXXXXX`）
- `{deep_agent_flag}`：用户开启 deepAgent 时填 `--deep-agent`，否则留空（即删掉该占位符）

### ③ 子 agent 报鉴权失败时的处理

若子 agent 返回鉴权失败（如 `❌ token 已过期`），主 agent 应：
1. 按 auth.md 重新执行登录流程（主 agent 自己完成，不再 spawn 子 agent 做）
2. 登录成功后，重新 spawn 一个新序号的子 agent 执行同一个分析命令
