# 结果呈现规则

## 基本原则

从返回 JSON 中取出 `markdown` 字段，**直接展示给用户，不要再次总结或二次加工**。

## 大象（Daxiang）频道图文混排

大象频道**支持图文混排的 Markdown 格式**，包括 `![名称](url)` 图片语法，图片会直接内联渲染。

子 agent 收到 markdown 后，**直接将完整 Markdown（含图片语法）一次性发送给用户，无需拆分**：

```python
message(action="send", message="🔗 会话链接：{chatUrl}\n\n{完整markdown原文}", channel=daxiang, target=...)
```

若 markdown 较长或有特殊原因需分段，也必须保持图文在同一段中，**不得把图片单独拆到末尾**。

## Chromium 未安装时的截图失败处理

若返回 JSON 中包含 `chromium_hint` 字段，说明本次有图表因 Chromium 未安装而无法渲染截图。

子 agent 必须：
1. **先发一条消息**：正常展示分析结论（`markdown` 字段原文，图表位置已显示占位文字）
2. **再发一条独立消息**：内容为 `chromium_hint` 字段的值，提示用户安装 Chromium

两条消息**顺序发送**，不要合并，不要在结论里内嵌安装提示。示例：

```python
# 第一条：分析结论
message(action="send", message="🔗 会话链接：{chatUrl}\n\n{markdown}", channel=daxiang, target=...)
# 第二条：截图失败原因（仅在 chromium_hint 存在时发）
message(action="send", message=result["chromium_hint"], channel=daxiang, target=...)
```

## ⛔ 严禁行为

1. **严禁推测会话 ID**：conversationId / chatUrl 必须完全来自接口实际返回的 JSON，禁止伪造或补全。
2. **严禁自行补全图表**：图表/可视化内容必须以接口实际返回结果为准。禁止自行额外编写绘图脚本并作为 BA-Agent 结果呈现。（例外：用户明确说「不调用 BA-Agent，自己画」时可以）
3. **严禁主 agent 自行构造 chatUrl / 分享链接**：展示给用户的链接必须完全来自子 agent 实际返回 JSON 中的 `chatUrl` 或 `shareUrl` 字段。若原始 JSON 没有链接字段，直接忽略跳链，不得自行拼接。
4. **严禁在规划模式下自行处理用户的修改/确认意图**：当用户意图为修改计划（replan）或确认执行计划（confirm_plan）时，主 agent 必须立即通过子 agent 调用 BA-Agent 服务执行，由子 agent 返回结果后再展示给用户。
5. **严禁展示非预期子 agent 的返回内容**：如意外触发了多个子 agent，只采用**第一个完成的、与当前操作对应的**子 agent 结果（`conversationId` 一致且操作类型匹配），其余一律丢弃。
6. **严禁把图片拆到末尾单独发送**：图片必须随 markdown 原文一起发出，保持图文混排效果。
