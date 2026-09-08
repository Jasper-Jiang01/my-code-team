# ~~规划模式（plan）详细规则~~

> ⛔ **【已过期，请勿使用】** 规划模式已正式下线，本文件仅作历史存档，不得再参考此文件执行任何操作。
> 当前规划相关的处理规则请见 [plan-deprecated.md](plan-deprecated.md)。

---

## 会话复用判断

**复用已有会话（传 `--conversation-id`）**，满足以下条件时：
- 用户正在对已生成的规划方案进行**修改**（如「去掉第二步」、「简化一下」）→ 使用 `--replan` + `--conversation-id`
- 用户**确认执行**当前方案（如「确认」、「执行」、「开始」）→ 使用 `confirm_plan`
- 用户对上一轮规划结果的追问、深挖，且话题未切换

🚨 **修改计划 / 确认执行计划，必须复用当前 conversationId，严禁新建会话！**

**新建会话**，满足以下条件时：
- 用户提出了全新的分析问题，与上一轮无关
- 用户明确说「重新规划」、「使用规划模式开个新的会话」
- 用户切换了分析模式（从 plan 切换到 analyze，反之亦然）—— 模式切换时也不能复用对方的 conversationId
- 上下文中没有 conversationId（第一次使用规划模式）

## 项目模式前置流程

当用户提到「在某个项目下规划」、「用项目XXX的数据」、「基于项目提问」时，**主 agent 必须先在本地完成以下步骤，不得跳过**：

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

**如果 `sourceList` 非空**，记录该列表，后续在 Step D 中通过 `--canglan-source-json` 直接传入 plan 命令。新流程下无需单独调用 `fetch_data` 取数，后端会在 streamV2 执行时自动取数。

**如果 `sourceList` 为空**：跳过此步，直接进入 Step C。

### Step C：创建会话（主 agent 执行）

```bash
python3 {cwd}/scripts/call_ba_agent.py create_conv \
  --name "分析问题前50字" \
  --mode plan \
  --project-id <projectId> \
  --ctx-json '<ctx 的 JSON 字符串>'
```

> 🚨 **必须传 `--ctx-json`（项目的 ctx）**，不得使用默认的空 ctx；`--project-id` 也必须同时传入。

### Step D：spawn 子 agent 执行规划

将 `sourceList`（JSON 字符串）通过 `--canglan-source-json` 传给子 agent：

```python
sessions_spawn(
  label="ba-conv-{conversationId}-1",
  mode="run",
  runTimeoutSeconds=1800,
  task="""你是 BA-Agent 执行专员。执行以下步骤，不读 SKILL.md，不做额外分析，不重鉴权：

Step 1：用 exec 执行规划命令：
  python3 {cwd}/scripts/call_ba_agent.py plan '用户问题' \
    --conversation-id {conversationId} \
    --project-id {projectId} \
    --canglan-source-json '{sourceList的JSON字符串}' \
    --no-reauth

Step 2：解析返回 JSON。
  - 若 status=needs_confirm：提取 markdown、conversationId、chatResponseId、plan 原始 JSON
  - 若直接返回结果：提取 chatUrl 和 markdown

Step 3：
  - needs_confirm 时：
    a. 将 plan 原始 JSON 写入 /tmp/ba_plan_{conversationId}_{chatResponseId}.json
    b. message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}\n\n---\n📋 `conversationId: {conversationId} | chatResponseId: {chatResponseId}`\n\n⬆️ 请回复「确认执行」开始分析，或告知需要修改的内容", channel=daxiang, target={daxiang_user_id})
  - 直接返回结果时：
    message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}", channel=daxiang, target={daxiang_user_id})
  **直接使用 markdown 原文，不做任何修改，图片语法 ![名称](url) 保持原样**

Step 4：**仅将约定好的固定短语回复给主 agent**：
  - needs_confirm 时：「✅ 已推送规划方案，conversationId={conversationId}，chatResponseId={chatResponseId}」
  - 直接完成时：「✅ 已完成分析并推送大象消息」
  - 失败时：「❌ 执行失败：{错误原文}」"""
)
```

> 如果 `sourceList` 为空（无沧澜数据集），省略 `--canglan-source-json` 参数。

---

## 非项目模式（无 project_id）

```bash
# Step A：主 agent exec 创建会话
python3 {cwd}/scripts/call_ba_agent.py create_conv --name "分析问题前50字" --mode plan
```

```python
# Step B：spawn 子 agent
sessions_spawn(
  label="ba-conv-{conversationId}-1",
  mode="run",
  runTimeoutSeconds=1800,
  task="""你是 BA-Agent 执行专员。执行以下步骤，不读 SKILL.md，不做额外分析，不重鉴权：

Step 1：用 exec 执行规划命令：
  python3 {cwd}/scripts/call_ba_agent.py plan '用户问题' \
    --conversation-id {conversationId} [--file ... --display-name ...] [--km-url ...] --no-reauth

Step 2：解析返回 JSON。
  - 若 status=needs_confirm：提取 markdown、conversationId、chatResponseId、plan 原始 JSON
  - 若直接返回结果：提取 chatUrl 和 markdown

Step 3：
  - needs_confirm 时：
    a. 将 plan 原始 JSON 写入 /tmp/ba_plan_{conversationId}_{chatResponseId}.json
    b. message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}\n\n---\n📋 `conversationId: {conversationId} | chatResponseId: {chatResponseId}`\n\n⬆️ 请回复「确认执行」开始分析，或告知需要修改的内容", channel=daxiang, target={daxiang_user_id})
  - 直接返回结果时：
    message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}", channel=daxiang, target={daxiang_user_id})
  **直接使用 markdown 原文，不做任何修改，图片语法 ![名称](url) 保持原样**

Step 4：**仅将约定好的固定短语回复给主 agent**：
  - needs_confirm 时：「✅ 已推送规划方案，conversationId={conversationId}，chatResponseId={chatResponseId}」
  - 直接完成时：「✅ 已完成分析并推送大象消息」
  - 失败时：「❌ 执行失败：{错误原文}」"""
)
# Step C：spawn 后主 agent 只发送一条「已提交任务，请稍候…」，之后不再等待或回复任何分析内容
```

---

## 返回格式

### 需要用户确认（status=needs_confirm）

```json
{
  "conversationId": "会话ID",
  "chatResponseId": "响应ID",
  "status": "needs_confirm",
  "plan": [
    { "id": "step1", "title": "步骤标题", "content": ["步骤内容..."] }
  ],
  "message": "规划模式需要用户确认计划后才能执行"
}
```

收到 needs_confirm 后，子 agent 必须：
1. 将 plan 原始 JSON 写入本地文件：`/tmp/ba_plan_{conversationId}_{chatResponseId}.json`
2. **用 message tool 直接推大象**，内容包含：`markdown` 原文 + 固定格式的元数据行 + 结尾提示
3. **不把规划方案内容回给主 agent**，只回简短的「✅ 已推送规划方案，conversationId=xxx，chatResponseId=xxx」
4. 主 agent 收到子 agent 的简短回复后，**不再做任何额外回复**，等待用户操作

**大象消息固定格式（needs_confirm 时）：**

```
🔗 会话链接：{chatUrl}

{markdown原文}

---
📋 `conversationId: {conversationId} | chatResponseId: {chatResponseId}`

⬆️ 请回复「确认执行」开始分析，或告知需要修改的内容
```

> `conversationId` 和 `chatResponseId` 以固定格式写在消息末尾，主 agent 在用户二次操作时从消息中解析这两个值。

### ⚠️ 主 agent 回复时机（关键）

**所有场景（首次规划 & 修改计划 & 确认执行）下，主 agent spawn 子 agent 后只发送一条「已提交任务，请稍候…」，之后不再等待、不再呈现任何分析或规划内容。**

- 子 agent 负责将规划方案、分析结果、错误信息全部**直接推大象**
- 主 agent **严禁自行模拟或重新展示**子 agent 的规划步骤、分析结论
- 若子 agent 报错，子 agent 将错误信息直接推送给用户，主 agent 无需额外处理

### 分支 A：用户确认执行

🚨 **必须复用当前 conversationId，不得新建会话！**

**主 agent 解析步骤：**
1. 从用户消息（或其引用的大象历史消息）中解析出 `conversationId` 和 `chatResponseId`（固定格式：`conversationId: xxx | chatResponseId: xxx`）
2. 读取本地文件 `/tmp/ba_plan_{conversationId}_{chatResponseId}.json` 获取 plan_json
3. **若文件不存在**：直接告知用户「⚠️ 规划方案已过期，请重新发起规划分析」，不再继续执行
4. 文件读取成功后，spawn 下一序号子 agent 执行 confirm_plan

```python
sessions_spawn(
  label="ba-conv-{conversationId}-{n+1}",
  mode="run",
  runTimeoutSeconds=1800,
  task="""你是 BA-Agent 执行专员。执行以下步骤，不读 SKILL.md，不做额外分析，不重鉴权：

Step 1：用 exec 执行确认命令：
  python3 {cwd}/scripts/call_ba_agent.py confirm_plan \
    {conversationId} {chatResponseId} --plan-json '{plan_json_原文}' --no-reauth

Step 2：解析返回 JSON，提取 chatUrl 和 markdown 字段（必须来自实际返回，不得伪造）。

Step 3：用 message tool 一次性发送完整图文内容：
  message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}", channel=daxiang, target={daxiang_user_id})
  **直接使用 markdown 原文，不做任何修改，图片语法 ![名称](url) 保持原样**

Step 4：发送完毕后，**仅将约定好的固定短语回复给主 agent**：
  - 成功：「✅ 已完成分析并推送大象消息」
  - 失败：「❌ 执行失败：{错误原文}」"""
)
```

> `--plan-json` 传入从本地文件读取的 plan JSON 字符串，**不得修改内容**。
> `chatResponseId` 必须从用户消息解析，**不得推测或伪造**。

### 分支 B：用户要求修改方案

🚨 **必须复用当前 conversationId，不得新建会话！**

修改计划时，主 agent 需从上下文（或用户引用的大象历史消息）中解析出原始的 `conversationId` 和 `chatResponseId`，并将原始 `chatResponseId` 传入子 agent task（变量名 `{originalChatResponseId}`）。

> 🐛 **NOTE（2026-04-03）**：replan 接口返回的 chatResponseId 可能是新 ID，疑似 bug，暂不使用。
> 规划方案文件始终以**原始** chatResponseId 命名，保证后续 confirm_plan 可正确读取。

spawn 下一序号子 agent：

```python
sessions_spawn(
  label="ba-conv-{conversationId}-{n+1}",
  mode="run",
  runTimeoutSeconds=1800,
  task="""你是 BA-Agent 执行专员。执行以下步骤，不读 SKILL.md，不做额外分析，不重鉴权：

Step 1：用 exec 执行修改命令：
  python3 {cwd}/scripts/call_ba_agent.py plan \
    '用户描述的修改要求（原文整理，不得改写）' \
    --conversation-id {conversationId} --replan --no-reauth

Step 2：解析返回 JSON。
  - 若 status=needs_confirm：提取 markdown、plan 原始 JSON
    # 注意：此处不使用返回的 chatResponseId，始终沿用主 agent 传入的原始值 {originalChatResponseId}
    # （原逻辑：chatResponseId = 返回 JSON 中的 chatResponseId —— 疑似 bug，暂注释）
    使用 chatResponseId = {originalChatResponseId}（由主 agent spawn 时传入，不得修改）
  - 若直接返回分析结果：提取 chatUrl 和 markdown

Step 3：
  - needs_confirm 时：
    a. 将 plan 原始 JSON 写入 /tmp/ba_plan_{conversationId}_{originalChatResponseId}.json（使用原始 chatResponseId）
    b. message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}\n\n---\n📋 `conversationId: {conversationId} | chatResponseId: {originalChatResponseId}`\n\n⬆️ 请回复「确认执行」开始分析，或告知需要修改的内容", channel=daxiang, target={daxiang_user_id})
  - 直接返回结果时：
    message(action=send, message="🔗 会话链接：{chatUrl}\n\n{markdown原文}", channel=daxiang, target={daxiang_user_id})
  **直接使用 markdown 原文，不做任何修改，图片语法 ![名称](url) 保持原样**

Step 4：**仅将约定好的固定短语回复给主 agent**：
  - needs_confirm 时：「✅ 已推送修改后规划方案，conversationId={conversationId}，chatResponseId={originalChatResponseId}」
  - 直接完成时：「✅ 已完成分析并推送大象消息」
  - 失败时：「❌ 执行失败：{错误原文}」"""
)
```

## 子 agent 执行规则

- 所有长时操作（plan / confirm_plan）通过 `mode="run"` 子 agent 执行
- 同一 conversationId 下，每次操作序号严格递增
- 子 agent 之间严格串行，不得并发

**子 agent task 中需替换的变量（spawn 时由主 agent 填入）：**
- `{conversationId}`：当前会话 ID
- `{projectId}`：项目 ID（项目模式时）
- `{sourceList的JSON字符串}`：project_info 返回的 sourceList JSON（项目+沧澜模式时）
- `{chatResponseId}`：确认执行时从用户消息解析出的响应 ID（首次规划返回的原始值）
- `{originalChatResponseId}`：修改方案时由主 agent 传入的**原始** chatResponseId（即首次规划返回的值，replan 后不覆盖）
- `{plan_json_原文}`：从本地文件 `/tmp/ba_plan_{conversationId}_{chatResponseId}.json` 读取的内容
- `{daxiang_user_id}`：用户的大象 ID（从 inbound_meta 的 chat_id 取，格式为 `user:XXXXXXX`）

### ③ 子 agent 报鉴权失败时的处理

若子 agent 返回鉴权失败（如 `❌ token 已过期`），主 agent 应：
1. 按 auth.md 重新执行登录流程（主 agent 自己完成，不再 spawn 子 agent 做）
2. 登录成功后，重新 spawn 一个新序号的子 agent 执行同一个命令
