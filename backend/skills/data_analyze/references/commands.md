# 全部可用子命令参考

> ⚠️ **调用任何子命令前，必须先完成鉴权校验（见 SKILL.md 强制前置规则）。**
> 动态编排场景同样不例外，auth 是所有操作的硬性前提。

> 📌 **关于 `{cwd}` 占位符**
> 本文档中所有命令示例里的 `{cwd}` 均代表本 skill 的根目录（即 `SKILL.md` 所在目录）。
> Agent 在实际执行前，必须将 `{cwd}` 替换为 skill 的实际绝对路径，例如：
> `~/.openclaw/skills/baa-basic`
> 如未替换直接执行，命令将因路径不存在而报错。

## 子命令一览

> ⛔ **`plan`、`confirm_plan` 已随规划模式正式下线，严禁调用**（详见 [plan-deprecated.md](plan-deprecated.md)）。脚本中仍保留这两个子命令的代码仅为向后兼容旧版本调用方，**不得在新的编排逻辑中使用**。

| 子命令 | 说明 | 是否需要子 agent |
|--------|------|----------------|
| `analyze` | 分析模式对话 | ✅ 需要（长时） |
| ~~`plan`~~ | ⛔ 已下线，禁止调用 | - |
| ~~`confirm_plan`~~ | ⛔ 已下线，禁止调用 | - |
| `share` | 生成会话分享链接 | ❌ 直接 exec |
| `share_to_km` | 写入学城（分享结果至学城），前置依赖用户提供写入学城哪个目录 | ❌ 直接 exec |
| `projects` | 获取项目列表（默认 authList，加 --personal 取个人项目） | ❌ 直接 exec |
| `history` | 获取对话消息历史 | ❌ 直接 exec |
| `create_conv` | 仅创建会话，返回 conversationId | ❌ 直接 exec |
| `user_info` | 获取当前登录用户信息（鉴权校验专用） | ❌ 直接 exec |
| `conversations` | 获取会话列表（查找历史会话） | ❌ 直接 exec |
| `conversation_detail` | 获取会话详情（含工作空间文件） | ❌ 直接 exec |
| `interrupt` | 中断正在执行的分析任务 | ❌ 直接 exec |
| `reconnect` | 流式断开后重连会话，继续等待分析结果（analyze 返回 ended=false 时；chatResponseId 可选） | ❌ 直接 exec |
| `evaluate` | 对回复点赞(1)或点踩(2) | ❌ 直接 exec |

## 命令示例

```bash
# 生成分享链接
python3 {cwd}/scripts/call_ba_agent.py share <conversationId>
# 返回：{"shareUrl": "https://ba-ai.sankuai.com/share/xxx"}

# 分享结果至学城（由 BA-Agent 后端直接写入，需提供目标学城页面 URL）
python3 {cwd}/scripts/call_ba_agent.py share_to_km <conversationId> \
  --title "报告标题" --km-url "https://km.sankuai.com/collabpage/xxx"

# 分享指定轮次的结果至学城（--round 指定第几轮，1-based）
# 分析模式：role=server 过滤后的第 N 个消息
# 规划模式：workspace 数组中第 N 位
python3 {cwd}/scripts/call_ba_agent.py share_to_km <conversationId> \
  --title "报告标题" --km-url "https://km.sankuai.com/collabpage/xxx" --round 2
# 若指定轮数超出范围，返回错误提示（不写入学城）：
# {"error": "指定的轮数 X 超出范围，当前会话共有 N 轮结果，请指定 1~N 之间的轮数"}

# 获取项目列表（默认：有权限使用的项目，调用 authList 接口）
python3 {cwd}/scripts/call_ba_agent.py projects

# 获取个人项目列表（用户明确说我的项目/个人项目时使用，调用 list 接口）
python3 {cwd}/scripts/call_ba_agent.py projects --personal

# 返回字段说明（projects 命令）：
# - projects: 当前页项目列表
# - pageNo: 当前页码
# - pageSize: 每页条数
# - count: 当前页实际返回条数
# - total: 总项目数
# ⚠️ 展示时必须告知用户：第 {pageNo} 页，共 {total} 条，本页 {count} 条

# 获取对话历史
python3 {cwd}/scripts/call_ba_agent.py history <conversationId> [--page 1] [--page-size 20]

# 创建会话（--mode 目前仅支持 analyze；plan 已下线，禁止使用）
python3 {cwd}/scripts/call_ba_agent.py create_conv --name "名称" --mode analyze

# 获取会话列表
python3 {cwd}/scripts/call_ba_agent.py conversations [--keyword 销售] [--page 1]

# 获取会话详情
python3 {cwd}/scripts/call_ba_agent.py conversation_detail <conversationId>

# 中断分析任务
python3 {cwd}/scripts/call_ba_agent.py interrupt <conversationId> <chatResponseId>

# 点赞 / 点踩
python3 {cwd}/scripts/call_ba_agent.py evaluate <messageId> 1   # 1=点赞, 2=点踩

# 流式断开续传（analyze 返回 ended=false 时调用；chatResponseId 可选，未提供则自动从历史消息取最新 id）
python3 {cwd}/scripts/call_ba_agent.py reconnect <conversationId> <chatResponseId>
python3 {cwd}/scripts/call_ba_agent.py reconnect <conversationId>              # 未带 chatResponseId 时自动兜底
python3 {cwd}/scripts/call_ba_agent.py reconnect <conversationId> <chatResponseId> --max-reconnect 10
# 返回：{"conversationId":"...","chatUrl":"...","markdown":"...","ended":true,"chatResponseId":"..."}
```

## 动态组合场景

**「帮我找一下上周做的销售分析会话」**
→ 鉴权 → `conversations --keyword 销售` 列出会话，展示给用户

**「分析还没完，帮我停掉」**
→ 鉴权 → 需要 conversationId + chatResponseId，先 `history` 获取 chatResponseId，再 `interrupt`

**「这个回答很好，帮我点个赞」**
→ 鉴权 → `history` 获取 messageId，再 `evaluate <messageId> 1`

**「帮我看看这个会话里的工作空间有哪些文件」**
→ 鉴权 → `conversation_detail <conversationId>`，解析 `workspace[0].file` 列表展示给用户

**「analyze 返回 ended=false（流式中断），需要继续等待结果」**
→ 鉴权 → 用 analyze 返回的 conversationId + chatResponseId 调 reconnect 续传：
  python3 {cwd}/scripts/call_ba_agent.py reconnect <conversationId> <chatResponseId> [--no-reauth]
→ reconnect 会再次走 reconnectV2 重连循环（默认 5 次，可 --max-reconnect 调整）；
  若 analyze 未返回 chatResponseId（一条消息都没收到就断），可不传该参数，脚本自动从 historyListV2 取最新 server 消息 id 兜底；
  若重连后仍无完整结论，脚本自动从 historyListV2 拉最新一轮 server 消息补全 summary。
→ 返回结构与 analyze 一致（含 ended/chatResponseId），若仍 ended=false 可再次调用。

**「帮我看看某个会话的结论 / 分析结果」**

> 📌 此场景为**查询历史会话**（可能是规划模式下线前遗留的旧会话），不涉及发起新的规划任务，与「plan 已下线」不冲突，可正常按 `data.mode` 做兼容读取。

→ 鉴权 → 先调 `conversation_detail <conversationId>`，从 `data.mode` 判断对话类型，再按以下规则提取结论：

| `data.mode` 值 | 对话类型 | 结论来源 | 提取方式 |
|---------------|---------|---------|---------|
| `common` | **分析模式** | `historyListV2` | 调 `history <conversationId>`，遍历 server 消息的所有 `content` segments，提取 `streamingEventKey` 含 `summary` 的 segments（type 为 markdown / chart / table），拼接后展示 |
| `intelCommon` | **规划模式（历史存量会话）** | `conversation_detail` workspace | 从 detail 的 `data.workspace[0].file` 中找 `type=result` 的文件，读取 `content.version.contents` 数组，遍历每项：`contentType=markdown` 直接拼接文本，`contentType=chart` 调 `chart_to_markdown` 处理，`contentType=table` 调 `table_to_markdown` 处理 |

> ⚠️ **注意**：使用 `conversation_detail` 中的 `data.mode` 字段作为判断依据（`common` vs `intelCommon`），不要使用 `ctx.mode` 或 `conversations` 列表里的 `mode` 字段（可能不一致）。

**分析模式结论提取伪代码：**
```python
# 1. 调 history 获取消息
messages = call history(conversationId)
# 2. 遍历所有 server 消息，收集 summary segments
summary_segs = []
for msg in messages:
    if msg.role != 'server': continue
    for seg in msg.content:
        key = seg.streamingEventKey or ''
        if 'summary' in key:
            summary_segs.append(seg)
# 3. format_content_to_markdown(summary_segs) → 最终结论 markdown
# 4. 如果 summary_segs 为空，降级为全量 server 消息内容
```

**规划模式结论提取伪代码（仅用于查询历史存量规划会话，不得用于发起新规划）：**
```python
# 1. 从 conversation_detail 的 workspace 找 result 文件
detail = call conversation_detail(conversationId)
files = detail.workspace[0].file
result_file = next((f for f in files if f.type == 'result'), None)
if not result_file:
    # 降级：直接展示 history 全量内容
    pass
contents = result_file.content.version.contents
# 2. 遍历 contents，按 contentType 处理
parts = []
for item in contents:
    if item.contentType == 'markdown': parts.append(item.data)
    elif item.contentType == 'chart':  parts.append(chart_to_markdown(item.data))
    elif item.contentType == 'table':  parts.append(table_to_markdown(item.data))
result_md = '\n\n'.join(filter(None, parts))
```

> 当用户需求不在预制流程里时，根据上表灵活组合调用。确实无法执行的操作，主动告知「不支持」并引导用户前往 https://ba-ai.sankuai.com/ 操作。
