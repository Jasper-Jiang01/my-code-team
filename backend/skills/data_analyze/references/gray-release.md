# 灰度链路配置

## 触发条件

用户描述中提到「灰度」、「灰度链路」、「灰度环境」、「gray release」、「走灰度」、「开启灰度」等，或明确提供了灰度链路标识（如 `gray-release-baagent-prod-test`）。

## 主 agent 提取规则

1. 如用户直接给出了完整的灰度标识（含 `gray-release-` 前缀），直接使用
2. 如用户只说「走灰度」等模糊描述，**必须追问一次**：「请提供灰度链路标识（如 `gray-release-baagent-prod-test`）」，不得猜测
3. 提取到标识后，通过 `--gray-release <标识>` 参数传给脚本（`create_conv`、`analyze`、`plan` 等所有命令均支持）

## 脚本行为

- 设置 `--gray-release` 后，**所有 HTTP 请求**（包括文件上传、接口调用、流式请求）都会自动在 Header 中添加 `gray-release-set: <标识>`
- 脚本会打印：`🔀 灰度链路已启用: gray-release-set=<标识>`
- 也可通过环境变量 `BA_GRAY_RELEASE=<标识>` 设置，无需每次传参

## 子 agent task 中的传递方式

主 agent spawn 子 agent 时，需在命令中加 `--gray-release <标识>`，例如：

```bash
python3 {cwd}/scripts/call_ba_agent.py analyze '分析问题' \
  --conversation-id {conversationId} \
  --gray-release gray-release-baagent-prod-test \
  --no-reauth
```
