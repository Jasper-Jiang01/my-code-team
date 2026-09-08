# 环境自检与准备（Step 0 详解，跨平台）

> 本文档是 SKILL.md「Step 0：确认工作环境 + 环境自检」的完整说明。将来新增不同环境/运行时的适配说明也统一放这里。

## 0.0 工作目录判定

- 如果当前工作目录为 `/workspace`，则此 skill 不适用，你自己直接完成用户任务即可，无需执行下面的自检。

## 0.1 判定 OS 并运行 bootstrap 自检脚本

检测 python / node，缺失则**停止流程**（不再自动安装），按下方 0.2 引导用户安装：

- **Windows** → `powershell -ExecutionPolicy Bypass -File <SKILL_DIR>/scripts/bootstrap.ps1`
- **macOS / Linux** → `bash <(tr -d '\r' < <SKILL_DIR>/scripts/bootstrap.sh)`

### OS 判定与 bash 兜底（必读，避免 Windows 误调 bash）

1. **OS 可从运行环境明确判定时**（检测到 Windows 注册表 / PowerShell 专属环境，或 `uname` 返回 Darwin/Linux），直接走对应分支。
2. **OS 不确定时，必须询问用户**：「请确认你的操作系统：Windows / macOS / Linux？」等用户回复后再走对应分支。**严禁**默认按某一种 OS 兜底（避免在 macOS/Linux 上误用 PowerShell，或在 Windows 上误调 bash）。
3. **Windows 一律走 PowerShell 分支，绝不调 bash**。Windows 上没有 POSIX bash，调用 `bash`（尤其进程替换 `bash <(...)`）会报 `No suitable shell found. CatPaw requires a Posix shell environment`。
4. **运行时兜底**：若已按某 OS 走 bash 分支但执行报错（如 `No suitable shell found`、`bash: command not found`），立即回退到 PowerShell 分支跑 `bootstrap.ps1`，不要重试 bash。

> 💡 **为何用 `bash <(tr -d '\r' < …)` 而非 `bash <SKILL_DIR>/scripts/bootstrap.sh`**：本 skill 可能通过非 git 方式分发（zip/拷贝），`.gitattributes` 的 LF 保障不生效。若 `bootstrap.sh` 被带成 CRLF 换行，直接 `bash` 会报 `syntax error: unexpected end of file`（bash 3.2 实测）。`tr -d '\r'` 在运行时剥掉回车、经进程替换喂给 bash，对 LF/CRLF 文件都能跑。脚本不依赖自身路径，可安全这样调用。

## 0.2 解析 bootstrap 的 stdout 契约块

解析 `BAA_BOOTSTRAP_BEGIN` 与 `BAA_BOOTSTRAP_END` 之间的内容：

```
OS=windows|macos|linux
PYTHON_CMD=python|python3|py|   # 记为 <PY>；py 表示后续用 `py -3`；空=缺失
PYTHON_VERSION=3.11.5|          # 空=未知/缺失
NODE_OK=true|false
NODE_VERSION=v18.17.0|          # 空=未知/缺失
READY=true|false
MANUAL_ACTIONS=Missing Python 3;Missing Node.js(>=14)   # 缺失项描述（英文，跨平台一致），可能为空
```

> `PYTHON_VERSION`/`NODE_VERSION` 仅用于人工排查参考（如向用户展示当前检测到的版本），流程判断只依赖 `PYTHON_CMD`/`NODE_OK`/`READY`，无需特殊处理这两个字段。

处理规则：
- **`READY=true`（退出码 0）**：记录 `<PY>` = `PYTHON_CMD` 的值，供本次会话后续所有命令使用（见 SKILL.md 核心约束第 0 条），继续 Step 1。
- **`READY=false`（退出码 10/11/12）**：环境不全，**立即停止流程**，不得继续后续 Step。
  > ⛔ **停止 ≠ 换个方式帮用户**：READY=false 后，除了「引导用户安装环境」外，**禁止**使用 Node.js / Python / 浏览器 / 其他 skill 等任何替代工具自行完成分析任务（与 SKILL.md「入口硬门槛」及「⚠️ 核心约束」的绝对禁止绕过条款一致）。「不继续 Step 1」的正确含义是「停下来引导用户装环境」，而**不是**「不走鉴权、改用别的方式硬做」。

  根据所在 agent 环境按下表引导用户：
  - **catdesk agent**（环境变量 `BA_AGENT=catpaw` 或探测到 `catdesk` CLI 可用）：调用 **env-setup** skill 安装所需环境。
    > ⚠️ **必须完整阅读并转达 env-setup skill 的 SKILL.md 中的提示信息，严禁自行简化、省略或凭记忆转述**（包括但不限于其中关于"安装完成后需重启 catdesk 才能生效"的说明）。env-setup 的提示文案以其 SKILL.md 实际内容为准，本文档不重复维护，避免后续不同步。若遗漏重启等关键提示，会导致用户重开会话后 bootstrap 自检依然返回 `READY=false`，反复提示缺少环境，误以为"装了却没生效"。
  - **其他 agent 环境**：告知用户「环境不全，{MANUAL_ACTIONS}。请手动安装 Python 3 与 Node.js(≥14)，安装后重开终端重新运行本 skill，或前往 https://ba-ai.sankuai.com 操作。」
- **DNS 不可达**（ba-ai.sankuai.com 无法解析）：告知用户环境异常并停止。

## 0.3 lx.js 埋点（依赖 node）

仅当 `NODE_OK=true` 时执行埋点；`NODE_OK=false` 时跳过所有 `node lx.js` 埋点调用（埋点为可选遥测，不得因此阻断主流程）。

**⚠️ 若 `NODE_OK=true`，优先执行 `node <SKILL_DIR>/scripts/lx.js skill start --custom user_prompt=<用户的原始prompt，不要总结概括>` 以标识 skill 执行开始。**
