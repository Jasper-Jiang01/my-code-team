---
name: codepilot-cloudnative-deploy
description: 将 CodePilot 的前后端一体化版本同步、提交并部署到 CatPaw CloudNative 测试环境。标准入口为 backend/deploy/deploy.sh（也支持 make deploy）。用户提到重新部署 CodePilot、部署测试环境、发布当前修改、更新测试站点或 CatPaw 部署时使用。
---

# CodePilot CloudNative 测试部署

## 固定信息

- 源码工作区：`/Users/jiang/Desktop/CodePilot `
- 后端目录：`/Users/jiang/Desktop/CodePilot /backend`
- 发布仓：`/Users/jiang/Desktop/CodePilot /jingwai-agent-main`
- 发布分支：`master`
- 部署类型：`cloudnative`
- 服务端口：`8000`
- 测试站点：`https://plus-jiangwenzhe02-codepilot.database.sankuai.com`
- 健康检查：`https://plus-jiangwenzhe02-codepilot.database.sankuai.com/api/health`

## 架构约束

1. FastAPI 同时提供 API 和 React 单页应用：`/api/*` 是动态接口，`/` 是前端页面。
2. CloudNative 构建镜像没有 `npm`。**不得**在 `.catpaw/catpaw_deploy.yaml` 的 `cmd` 中执行 `npm`、`npm ci` 或 `npm run build`。
3. 前端必须先在同步机上构建，再放入 `src/codepilot/api/static/`：
   - 同步脚本：`backend/scripts/sync_catpaw.sh`
   - 静态文件路径：`src/codepilot/api/static/index.html` 和 `src/codepilot/api/static/assets/`
4. 密钥策略：CatPaw CloudNative（无 appkey）**没有环境变量注入机制**（无管理控制台，
   catpaw_deploy.yaml 无 env 字段，已向官方手册与部署后端求证）。因此本发布仓作为
   **个人测试仓**例外处理：`backend/.env` 会随同步复制到仓库根并强制提交
   （sync_catpaw.sh 复制 + deploy.sh `git add -f`），`server.py` 启动时由
   `load_dotenv` / pydantic `env_file` 双机制读取。切勿将此做法用于团队仓或线上仓。

## 日常重新部署流程

### 标准入口：deploy.sh（推荐）

```bash
bash "/Users/jiang/Desktop/CodePilot /backend/deploy/deploy.sh" ["<提交信息>"]
```

脚本自动执行本节全部步骤：预检 → 同步 → 校验 → 提交推送 → 部署触发 → 切流轮询验收，任一步失败立即停止并报告原始错误。

部署触发说明：本机没有部署 CLI。脚本推送完成后会提示用 CatPaw（`catpaw_deploy` 工具）对发布仓触发 CloudNative 部署，随后持续轮询线上切流状态（默认最长 15 分钟，`DEPLOY_WAIT_MIN` 可调）。Agent 协作方式：后台运行脚本，看到提示后触发 `catpaw_deploy`，脚本自动完成验收。若设置了 `DEPLOY_TRIGGER_CMD` 环境变量，脚本会在约 4 分钟未切流时自动重触发一次。

其他模式：

- `bash deploy.sh --dry-run`：预演（同步 + 校验，不提交、不推送、不部署）
- `bash deploy.sh verify`：仅验收线上（健康检查 + 切流校验，单次详细报告）

预检规则：发布仓允许存在「同步管理范围内」的未提交改动（上次未完成的同步产物）；范围之外的未提交改动可能是用户手改，同步会覆盖，脚本会拒绝执行并要求先人工处理。

### 手动流程（脚本所实现的细节，脚本不可用时备用）

按以下顺序执行，任一步失败即停止并报告原始错误，不要跳过失败继续部署。

1. 检查源码与发布仓状态，确认不会覆盖用户未提交的发布仓改动。
2. 运行同步命令。它会执行本地 `npm ci`、构建前端、同步后端与静态产物：

```bash
cd "/Users/jiang/Desktop/CodePilot /backend" && make sync-catpaw
```

3. 验证构建结果：
   - `jingwai-agent-main/src/codepilot/api/static/index.html` 存在。
   - `jingwai-agent-main/src/codepilot/api/static/assets/` 存在。
   - `python3 -m compileall -q` 校验 `backend/src/codepilot/api/app.py` 和发布仓同路径文件。
   - 检查 `git diff --check`。
4. 检查发布配置：`.catpaw/catpaw_deploy.yaml` 必须满足：
   - `type: cloudnative`
   - `python: "3.12"`
   - `runCmd` 包含 `python server.py`
   - `ports` 第一个端口为 `8000`
   - `cmd` 不包含 `npm`
5. 仅在同步与校验成功后，在发布仓提交所有由同步产生的源码、部署配置及 `src/codepilot/api/static/` 静态产物，并推送：

```bash
cd "/Users/jiang/Desktop/CodePilot /jingwai-agent-main"
git add -A
git commit -m "<清晰描述本次改动>"
git push origin master
```

若没有文件改动，不创建空提交；可直接继续部署现有 `master`。

6. 对发布仓调用 CatPaw CloudNative 测试部署。不要传泳道；部署完成后记录任务链接。
7. 成功后验证：

```bash
curl -fsS https://plus-jiangwenzhe02-codepilot.database.sankuai.com/api/health
curl -fsS https://plus-jiangwenzhe02-codepilot.database.sankuai.com/ | head -20
```

预期：健康检查返回 `{"status":"ok"}`，根路径返回 `200` 与 HTML，且不能包含 `frontend build is unavailable`。
注意：根路径不支持 HEAD（返回 405，仅 GET），不要用 `curl -I`。

版本切流校验（必做）：HTML 引用的资源 hash 必须与本次同步产物一致（对比
`src/codepilot/api/static/assets/` 下的文件名），且新资源直接可访问：

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://plus-jiangwenzhe02-codepilot.database.sankuai.com/assets/<本次新hash>.css
```

新 hash 返回 200 才算切流完成；若返回 404 且 HTML 仍引用旧 hash，见“失败处理”中的未切流条目。

## 失败处理

- `npm: command not found`：确认 CloudNative YAML 的 `cmd` 中没有 npm；重新执行同步，使静态产物进入 `src/codepilot/api/static/` 后再发布。
- `frontend build is unavailable`：检查发布仓的 `src/codepilot/api/static/index.html` 是否被提交；确认 `app.py` 从模块相邻的 `static` 目录读取。
- 根路径无响应：核对 `ports` 的第一个值是 `8000`，并确保 `python server.py` 监听 8000。
- 部署工具不返回站点链接：使用固定测试站点与健康检查 URL 验证；同时返回构建任务链接。
- 构建或部署失败：不要自动重复尝试；只返回任务链接和平台给出的失败原因，等待用户提供日志或要求继续。
- 部署返回成功但站点仍是旧版本（未切流，2026-09-04 实遇）：
  - 症状：根路径 HTML 仍引用旧资源 hash（旧 js/css 返回 200），新 hash 资源 404，`/api/health` 正常。
  - 诊断：① 查旧资源响应头 `Last-Modified`，若早于本次发布提交时间，说明在线实例未更新；② 带随机 query 参数重试新 hash，排除 CDN 负缓存（404 响应含 `x-envoy-upstream-service-time`，说明是 upstream 真实返回，非缓存）。
  - 处理：先等待 3~5 分钟排除实例滚动替换中；仍未切流则在同一 master 提交上重新触发一次 CatPaw CloudNative 部署（实测二次任务 30 秒内切流成功），记录新任务链接并重新验收。`deploy.sh` 已内置该轮询与自动重触发逻辑（有 `DEPLOY_TRIGGER_CMD` 时全自动，否则提示 Agent 用 catpaw_deploy 重触发后跑 `deploy.sh verify`）。

## 输出格式

成功时报告：发布提交、构建任务链接、测试站点、`/api/health` 验证结果。

失败时报告：构建任务链接和原始失败原因；不要臆测原因。
