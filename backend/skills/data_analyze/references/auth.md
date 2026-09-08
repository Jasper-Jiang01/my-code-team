# 鉴权说明

> 📌 **`python3` 命令名占位提醒**：本文档所有 `python3` 均为占位符。Windows 下须替换为 `python`（或 `py -3`），mac/linux 保持 `python3`。详见 SKILL.md 核心约束第 0 条。

## 鉴权方式选择（顺序按 OS 定制）

鉴权顺序**由脚本 `call_ba_agent.py` 内部按操作系统自动决定**（`_reauth()` 中的 `_SKIP_CIBA` 逻辑），主 agent 无需手动干预顺序：

| OS | 鉴权顺序 | 说明 |
|----|---------|------|
| **Linux / Ubuntu** | MOA → CIBA → Playwright | 完整三级降级 |
| **macOS / Windows** | MOA → Playwright | **跳过 CIBA**（CIBA 在 mac/win 有已知问题，不稳定） |

> 🐾 **catpaw agent 例外（不分 OS）**：当运行在 catpaw agent 环境时，鉴权顺序为 **MOA → CatDesk**，**跳过 CIBA 与 Playwright**。CatDesk 内置浏览器可复用用户已登录的 SSO 会话，无需扫码或手动登录。判定逻辑在 `call_ba_agent.py` 的 `_is_catpaw_agent()`：优先看环境变量 `BA_AGENT=catpaw`，兜底探测 `catdesk` CLI 是否可用。此分支优先级高于上表的 OS 顺序。

触发重新登录前，**主 agent 必须先判断 MOA 是否可用**，决策流程如下：

```
触发重新登录
     │
     ▼
inject-ba-cookie-moa.py
（内部自动：检查包 → 安装 → 探测 → 换票）
     │ 退出码 0（MOA 鉴权成功）
     ▼
继续执行
     │ 退出码 1（安装失败 / MOA 未启动未登录 / 换票失败）
     │
     ├── catpaw agent ─→ 降级：python3 {cwd}/scripts/inject-ba-cookie-catdesk.py <misId> [--env prod]
     │                        │ 退出码 0（CatDesk 鉴权成功）→ 继续执行
     │                        │ 退出码非 0 → 停止（不再降级 CIBA/Playwright），提示用户在 CatDesk 登录 SSO
     │
     ├── Linux/Ubuntu ─→ 降级：python3 {cwd}/scripts/inject-ba-cookie-supabase-ciba.py <misId>
     │                        │ 退出码 0（CIBA 鉴权成功）→ 继续执行
     │                        │ 退出码非 0（CIBA 也失败）→ 降级 Playwright（见下）
     │
     └── macOS/Windows ─→ 跳过 CIBA，直接降级 Playwright（见下）
     ▼
降级：python3 {cwd}/scripts/inject-ba-cookie-playwright.py <misId> [--env prod]
（打开本地 Chrome 浏览器，用户手动登录后自动提取 cookie）
```

> 🐾 catpaw 分支由 `_reauth()` 在 MOA 失败后优先判定（`_IS_CATPAW`），命中即走 CatDesk，不进入下方 CIBA/Playwright 分支。

`inject-ba-cookie-moa.py` **内部自动完成**以下步骤：
1. 检查 `scripts/node_modules/.bin/mtsso-moa-feature-probe` 是否存在
2. 不存在则执行：`npm install @mtfe/mtsso-auth-official@latest --registry http://r.npm.sankuai.com --prefix <{cwd}/scripts/>`
3. 安装失败 → 退出码 1
4. 执行 `mtsso-moa-feature-probe -t 3` 探测 MOA 可用性
5. 探测失败（退出码非 0）→ 退出码 1
6. 执行 `mtsso-moa-local-exchange --audience 07b8be90c9` 换票
7. 换票失败 → 退出码 1
8. 成功 → token 写入 `~/.cache/ba-analysis/access_token.txt`，退出码 0

| 情况 | 使用脚本 | 是否需要 misId | 适用 OS |
|------|---------|----------------|---------|
| MOA 可用且换票成功 | `inject-ba-cookie-moa.py` | ❌ 不需要 | 全部 |
| **catpaw agent** MOA 失败 | `inject-ba-cookie-catdesk.py` | ✅ 需要（可选） | **仅 catpaw 环境** |
| MOA 失败，CIBA 可用 | `inject-ba-cookie-supabase-ciba.py` | ✅ 需要 | **仅 Linux/Ubuntu**（非 catpaw） |
| MOA 失败（mac/win）或 MOA+CIBA 均失败 | `inject-ba-cookie-playwright.py` | ✅ 需要（可选） | 全部（非 catpaw） |

> - 三种方式最终都将 access_token 写入运行时状态目录（`~/.cache/ba-analysis/`；Windows 为 `%LOCALAPPDATA%\ba-analysis\`），互不干扰。
> - **macOS / Windows 跳过 CIBA**：MOA 失败后直接进 Playwright，脚本内部已自动处理。

---

## 登录状态校验（每次分析任务前必做）

每次用户发起分析类请求（analyze，**不包括已下线的 plan / confirm_plan**）前，**主 agent 必须先执行 `user_info` 子命令校验登录状态**：

```bash
MIS=$(python3 -c "
import os
mis = os.environ.get('MIS_ID') or os.environ.get('MIS_ID_HINT') or os.environ.get('SANDBOX_MIS')
if not mis:
    try: mis = open(os.path.expanduser('~/.cache/ba-analysis/mis_id.txt')).read().strip()
    except: pass
print(mis or '')
" 2>/dev/null)

if [ -n "$MIS" ]; then
  python3 {cwd}/scripts/call_ba_agent.py user_info --expected-mis "$MIS"
else
  python3 {cwd}/scripts/call_ba_agent.py user_info
fi
```

**退出码处理：**

| 退出码 | 含义 | 主 agent 行为 |
|--------|------|---------------|
| `0` | 已登录且用户匹配 | 正常继续后续步骤 |
| `2` | 未登录 / token 无效 | 停止分析，**主动执行登录脚本**（见下方），不要要求用户手动执行 |
| `3` | 登录用户与当前用户不符（已自动清除 token） | 停止分析，**主动执行登录脚本**（见下方） |

---

## 重新登录

退出码非 0 时，执行以下完整登录逻辑：

### 步骤 A：尝试 MOA 本地鉴权

```bash
python3 {cwd}/scripts/inject-ba-cookie-moa.py
```

`inject-ba-cookie-moa.py` 内部流程（无需外部干预，全自动）：
1. 检查 `npx` 是否可用（未找到则退出码 `1`）
2. 通过 `npx --no-install mtsso-moa-feature-probe --help` 探测工具是否可用
3. 工具不可用 → 退出码 `1`（调用方降级 CIBA）
4. 执行 `npx mtsso-moa-feature-probe -t 3` 探测 MOA 可用性
5. 探测失败（退出码非 0）→ 退出码 `1`
6. 探测通过后，执行 `npx mtsso-moa-local-exchange --audience 07b8be90c9` 换票
7. 成功则保存 token 至 `~/.cache/ba-analysis/access_token.txt`，退出码 `0`
8. 任一步骤失败则退出码 `1`

**`inject-ba-cookie-moa.py` 退出码：**

| 退出码 | 含义 |
|--------|------|
| `0` | MOA 鉴权成功，继续后续分析 |
| `1` | 失败（安装失败 / MOA 未登录 / 换票失败），降级 CIBA |

### 步骤 B0：降级 CatDesk 鉴权（MOA 失败时，**仅 catpaw agent 环境**）

> 🐾 当 `_IS_CATPAW` 为真（`BA_AGENT=catpaw` 或探测到 `catdesk` CLI）时，MOA 失败后**只走本步骤**，不进入步骤 B（CIBA）/步骤 C（Playwright）。此逻辑由 `call_ba_agent.py` 的 `_reauth()` 自动处理，无需主 agent 手动判断。

```bash
python3 {cwd}/scripts/inject-ba-cookie-catdesk.py <misId> --env prod
```

**CatDesk 流程（`inject-ba-cookie-catdesk.py` 内部逻辑）：**
1. 检查 `catdesk` CLI 是否可用（Windows 优先 `catdesk.cmd`）
2. 通过 `catdesk browser-action` 导航到 BA-Agent 页面（复用 CatDesk 内置浏览器的 SSO 登录态）
3. `cookies_get` 提取 `07b8be90c9_ssoid`（找不到则回退 `duolaam_ssoid`）
4. token 保存至运行时状态目录（`~/.cache/ba-analysis/`；Windows 为 `%LOCALAPPDATA%\ba-analysis\`），退出码 `0`
5. 任一步骤失败 → 退出码 `1`

**CatDesk 退出码非 0 时，停止（不再降级），提示用户在 CatDesk 中完成美团 SSO 登录后重试。**

### 步骤 B：降级 CIBA 鉴权（MOA 失败时，**仅 Linux/Ubuntu，非 catpaw**）

> ⚠️ **macOS / Windows 跳过本步骤**，MOA 失败后直接进入步骤 C（Playwright）。此逻辑由 `call_ba_agent.py` 的 `_reauth()` 自动处理（`_SKIP_CIBA`），无需主 agent 手动判断。

```bash
python3 {cwd}/scripts/inject-ba-cookie-supabase-ciba.py <misId>
```

misId 获取优先级：
1. 环境变量 `MIS_ID` / `MIS_ID_HINT` / `SANDBOX_MIS`
2. `~/.cache/ba-analysis/mis_id.txt`（MOA/CIBA 成功登录后会自动写入）
3. 以上都无 → 询问用户

**CIBA 流程（`inject-ba-cookie-supabase-ciba.py` 内部逻辑）：**
1. `POST /api/sandbox/sso/ciba-auth` → 触发大象 App 推送授权通知
2. 每 5s 轮询 `POST /api/sandbox/sso/ciba-token`，最多 3 分钟
3. `POST /api/sandbox/sso/exchange-token-by-client-ids` 换票（audience: `07b8be90c9`）
4. token 保存至 `~/.cache/ba-analysis/access_token.txt`

> ⚠️ BA-Agent 的 `client_id` 当前为 `07b8be90c9`。若换票失败，提示用户联系 BA-Agent 系统管理员确认正确的 clientId。

**CIBA 退出码非 0 时，继续降级到步骤 C。**

### 步骤 C：降级 Playwright 浏览器登录（MOA + CIBA 均失败时）

> 此方式适用于 Claude Code 等本地桌面环境，会打开系统 Chrome 浏览器让用户手动登录。

```bash
python3 {cwd}/scripts/inject-ba-cookie-playwright.py <misId> --env prod
```

**Playwright 流程（`inject-ba-cookie-playwright.py` 内部逻辑）：**
1. 确保 `playwright` Python 包可用（未安装时自动安装）
2. 通过 Playwright 启动系统 Chrome 浏览器（非 headless），打开 BA-Agent 页面
3. 用户在浏览器中完成 SSO 登录
4. 每 2 秒轮询页面 cookie，检测目标 cookie（`07b8be90c9_ssoid`）
5. 检测到 token → 保存至 `~/.cache/ba-analysis/access_token.txt`，退出码 `0`
6. 超时（默认 300 秒）→ 退出码 `1`

**参数说明：**
- 第一个参数：misId（可选，用于记录）
- `--env`：环境选择，脚本自身 argparse 允许 `prod`（默认）/ `st` / `test`，但 **本 skill 业务层不支持 test 环境**（`call_ba_agent.py` 无 test 域名映射，SKILL.md 已声明「不支持 test 环境」），**实际调用时只能传 `prod` 或 `st`**，不得传 `test`
- `--timeout`：等待登录超时秒数（默认 300）

**注意事项：**
- 需要本地已安装 Chrome 浏览器
- 会弹出浏览器窗口，仅适用于有桌面环境的场景（如 Claude Code 本地运行）
- 在无桌面的服务端环境（如 sandbox）中不可用，此时应提示用户前往 https://ba-ai.sankuai.com 手动操作

**连续三种方式均失败后，停止重试，告知用户并提示联系 BA-Agent 系统管理员。**

---

## ⚠️ 子 agent 内禁止重鉴权

**子 agent（BA-Agent 执行专员）遇到 401/403 或 token 过期时，必须直接以非 0 退出码终止并报错返回，不得自行触发登录流程。**

鉴权职责归属：
- **主 agent**：负责 spawn 子 agent 前的鉴权保障；负责子 agent 报鉴权失败后的补救登录
- **子 agent**：只负责执行分析命令，遇到鉴权问题立即报错，由主 agent 处理

子 agent task 中应包含 `--no-reauth` 参数（调用脚本时禁用自动重鉴权），并明确告知：「不重鉴权，失败时将错误原文回复」。

---

## 异常处理

| 情况 | 处理方式 |
|------|---------|
| MOA 安装失败 | 降级 CIBA |
| MOA 探测不可用（未启动/未登录） | 降级 CIBA |
| MOA 换票失败 | 降级 CIBA |
| CIBA 失败 | 降级 Playwright 浏览器登录 |
| Playwright 失败（无桌面/超时） | 停止重试，提示用户联系管理员或手动访问 BA-Agent 平台 |
| 子 agent 报鉴权失败（401/403） | **主 agent** 主动执行登录流程（MOA→CIBA→Playwright），登录后重新 spawn 子 agent |
| 换票失败（clientId 问题） | 提示联系管理员确认 `07b8be90c9` 是否正确 |
| 分析超时 | 告知用户后台仍在运行，可通过 `history` 查询结果 |
| SSL EOF / TLS 连接中断（鉴权校验或文件上传时） | 属底层网络抖动，**自动重试 1 次**同一命令；仍失败则告知用户网络不稳定，引导其检查网络后重试或前往 https://ba-ai.sankuai.com 手动操作。**不得**因此改用其他工具自行分析 |
| Python `externally-managed-environment` 导致依赖/OpenSSL 不可用 | 属运行环境限制，非用户问题。告知用户当前 Python 环境受限无法安装依赖，引导前往 https://ba-ai.sankuai.com 操作或联系管理员，**不得**绕过流程自行分析 |

---

## 鉴权失败埋点（`auth-failed`）

`call_ba_agent.py` 的 `_reauth()` 在**所有降级路径都已尝试完仍未拿到 token**（即真正判定为「鉴权没通过」）的三个终止点，统一上报一次 `auth-failed` step，用于统计「鉴权没通过」的 case 数与原因分布，避免只看各级 `*.success`/`*.failure` 明细而无法直接得出漏斗最终失败率：

| 触发时机 | `fail_reason` | 其他 custom 参数 | 说明 |
|---------|---------------|------------------|------|
| `_NO_REAUTH`（子 agent 传了 `--no-reauth`，遇到 token 过期直接报错） | `no_reauth_disabled` | `reason`（触发重鉴权的原因文案） | 这类不是真正走完降级链路后失败，而是设计上刻意跳过重鉴权，统计时建议单独筛出，不与下面两种原因混算「鉴权成功率」 |
| catpaw 环境下 MOA + CatDesk 均失败（不再降级 CIBA/Playwright） | `catdesk_failed` | `last_method=catdesk`、`error_reason`（CatDesk 具体错误码）、`moa_error_reason`（MOA 具体错误码） | catpaw 专属分支的终态失败 |
| 非 catpaw 环境下 MOA → CIBA → Playwright 三级全部失败 | `all_methods_failed` | `last_method=playwright`、`error_reason`（Playwright 具体错误码）、`moa_error_reason`、`ciba_error_reason` | 标准三级降级链路的终态失败 |

### 具体根因（`error_reason` / `*_error_reason`）

`fail_reason`/`last_method` 只能说明「卡在哪一级」，无法说明「这一级为什么失败」。为此，四个鉴权子脚本（`inject-ba-cookie-{moa,catdesk,supabase-ciba,playwright}.py`）在各自的失败分支，会把机器可读的错误码写入调用方通过环境变量 `BA_AUTH_ERROR_FILE` 指定的临时文件（不影响原有的面向用户的中文 `print` 输出）；`_reauth()` 内的 `_run_auth_script()` 负责读取该文件并清理，作为 `error_reason` 传给对应的 `auth-*.failure` 与 `auth-failed` 埋点。

**MOA（`error_reason` 取值）：**

| 值 | 含义 |
|----|------|
| `moa_npx_not_found` | 本机未检测到 `npx`（未安装 Node.js） |
| `moa_tool_not_installed` | `@mtfe/mtsso-auth-official` 工具未就绪 |
| `moa_probe_unavailable` | `mtsso-moa-feature-probe` 探测失败（MOA 未启动/未登录） |
| `moa_exchange_failed` | `mtsso-moa-local-exchange` 换票失败 |
| `moa_token_extract_failed` | 换票响应中无法提取 `access_token` |

**CatDesk（`error_reason` 取值，仅 catpaw 环境）：**

| 值 | 含义 |
|----|------|
| `catdesk_cli_unavailable` | `catdesk` CLI 不可用/未加入 PATH |
| `catdesk_navigate_failed` | 导航到 BA-Agent 页面失败 |
| `catdesk_cookie_not_found` | 未在页面 cookie 中提取到 `ssoid`（多半是未登录 SSO） |

**CIBA（`error_reason` 取值，仅非 catpaw 的 Linux 环境）：**

| 值 | 含义 |
|----|------|
| `skipped_mac_win` | macOS/Windows 平台跳过 CIBA（非真实失败） |
| `no_mis_id` | 未拿到 misId，跳过 CIBA（非真实失败） |
| `ciba_identifier_missing` | 环境变量 `IDENTIFIER` 未设置 |
| `ciba_auth_request_failed` | `ciba-auth` 接口请求异常 |
| `ciba_auth_req_id_failed` | 未获取到 `auth_req_id` |
| `ciba_confirm_timeout` | 3 分钟内用户未在大象确认授权 |
| `ciba_exchange_request_failed` | 换票接口请求异常 |
| `ciba_exchange_failed` | 换票失败（接口返回空/失败） |
| `ciba_exchange_format_unknown` | 换票响应格式不符合预期 |
| `ciba_token_extract_failed` | 无法从换票响应中提取 token 值 |

**Playwright（`error_reason` 取值）：**

| 值 | 含义 |
|----|------|
| `playwright_pkg_install_failed` | `playwright` Python 包安装失败 |
| `playwright_invalid_env` | 传入了不支持的 `--env` |
| `playwright_no_browser` | 未找到可用浏览器（Chrome/Edge/Firefox/内置 Chromium 均不可用） |
| `playwright_login_timeout` | 超时（默认 300s）未检测到登录 cookie，用户可能未完成登录 |
| `playwright_user_interrupted` | 用户主动中断（Ctrl+C） |
| `playwright_exception` | 浏览器启动/运行期间发生未分类异常 |

同时补齐了 Playwright 分支原本缺失的 `auth-playwright.success` / `auth-playwright.failure` 埋点，使三级降级链路（MOA / CatDesk / CIBA / Playwright）在 success、failure 维度上均完整可查，可据此计算「降级到第 N 级的转化率」及「最终失败率」。

**统计口径建议：**
- 鉴权最终失败率 = `auth-failed`（`fail_reason != no_reauth_disabled`）/ `auth-start`
- 按 `fail_reason` / `last_method` 下钻可区分是 catpaw 分支失败还是标准三级降级全失败
- 按 `error_reason`（及 `moa_error_reason`/`ciba_error_reason`）下钻可定位具体根因（如「MOA 未装工具」占比 vs 「Playwright 登录超时」占比），用于针对性优化对应环节的引导文案或自动化程度
- `no_reauth_disabled` 的 case 反映的是子 agent 场景下 token 提前过期或未续期，应结合主 agent 重鉴权后是否重新 spawn 成功来看，不直接计入用户侧鉴权失败率
