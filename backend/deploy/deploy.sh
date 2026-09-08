#!/usr/bin/env bash
# =============================================================================
# CodePilot CloudNative 测试环境自动部署
#
# 实现 backend/deploy/DEPLOY.md 的完整流程：
#   预检 -> 同步（make sync-catpaw）-> 产物与配置校验 -> 提交推送
#   -> 部署触发 -> 切流轮询验收（未切流自动重触发一次）
# 任一步失败立即停止并报告原始错误（对齐 DEPLOY.md：不要跳过失败继续部署）。
#
# 用法：
#   bash deploy.sh ["<提交信息>"]    完整部署
#   bash deploy.sh --dry-run [msg]   预演：同步 + 校验，不提交 / 不推送 / 不部署
#   bash deploy.sh verify            仅验收线上（健康检查 + 切流校验）
#
# 环境变量：
#   DEPLOY_TRIGGER_CMD  部署触发命令（可选）。本机没有部署 CLI，默认不设置：
#                       脚本推送完成后会提示用 CatPaw（catpaw_deploy 工具）对
#                       发布仓触发 CloudNative 部署，随后持续轮询切流状态。
#                       Agent 协作方式：后台运行本脚本，看到提示后触发部署即可。
#   DEPLOY_WAIT_MIN     切流总等待时长（分钟，默认 15）。
# =============================================================================

set -euo pipefail

# ---------- 固定信息（与 DEPLOY.md 保持一致） ----------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # backend/deploy
BACKEND="$(cd "$SCRIPT_DIR/.." && pwd)"       # backend
ROOT="$(cd "$BACKEND/.." && pwd)"             # CodePilot 根目录
DEST="$ROOT/jingwai-agent-main"               # 发布仓
BRANCH="master"
SITE="https://plus-jiangwenzhe02-codepilot.database.sankuai.com"

POLL_INTERVAL=20                                     # 切流轮询间隔（秒）
RETRY_AFTER=240                                      # 未切流自动重触发阈值（秒，对齐 DEPLOY.md 的 3~5 分钟）
TOTAL_WAIT=$(( ${DEPLOY_WAIT_MIN:-15} * 60 ))        # 切流总等待（秒）

COMMIT_SHA=""
REPO_REFS=""
PUSH_EPOCH=0

# ---------- 参数解析 ----------
MODE="full"
MSG=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    verify)    MODE="verify" ;;
    *)         MSG="$arg" ;;
  esac
done

log()  { printf '[deploy] %s\n' "$*"; }
warn() { printf '[warn]    %s\n' "$*"; }
die()  { printf '[error]   %s\n' "$*" >&2; exit 1; }

trap 'rc=$?; if [ "$rc" -ne 0 ]; then printf "[error]   deploy.sh 中断（exit %d）。按 DEPLOY.md：任一步失败即停止，不要跳过失败继续部署。\n" "$rc" >&2; fi' EXIT

# ---------- 0. 清理本地垃圾文件（不应进入发布仓） ----------
clean_junk() {
  find "$DEST" -maxdepth 2 -name '.DS_Store' -delete 2>/dev/null || true
  rm -rf "$DEST/frontend/.screenshots"
}

# ---------- 1. 预检 ----------
precheck() {
  [ -d "$BACKEND/src/codepilot" ] || die "源码目录缺失：$BACKEND"
  [ -d "$DEST/.git" ] || die "发布仓缺失：$DEST"

  local branch
  branch="$(git -C "$DEST" branch --show-current)"
  [ "$branch" = "$BRANCH" ] || die "发布仓当前分支为 '$branch'，应为 $BRANCH"

  # 发布仓允许存在「同步管理范围内」的未提交改动（多为上次未完成的同步产物）；
  # 范围之外的改动可能是用户手改，rsync --delete 会将其覆盖，必须先人工处理。
  # .env 在白名单内：本仓为个人测试仓，密钥随仓提交（CloudNative 无环境变量注入）。
  local suspicious
  suspicious="$(git -C "$DEST" status --porcelain | cut -c4- \
    | grep -vE '^(src/|skills/|frontend/|checkpoints/|agent/|server\.py|requirements\.txt|manifest\.yaml|\.env|\.env\.example|package\.json|README\.md|\.gitignore|\.catpaw/)' || true)"
  if [ -n "$suspicious" ]; then
    die "发布仓存在同步范围之外的未提交改动（可能为用户手改，同步会覆盖它们）：
$suspicious
请先在发布仓提交或还原这些改动后重试。"
  fi
  log "预检通过：发布仓位于 $BRANCH，无同步范围外的未提交改动"
}

# ---------- 2. 同步 ----------
sync_all() {
  log "执行同步（本地 npm ci + 前端构建 + rsync 后端与静态产物）"
  (cd "$BACKEND" && make sync-catpaw) || die "make sync-catpaw 失败"
}

# ---------- 3. 校验 ----------
validate() {
  [ -f "$DEST/src/codepilot/api/static/index.html" ] || die "缺少 src/codepilot/api/static/index.html"
  [ -d "$DEST/src/codepilot/api/static/assets" ] || die "缺少 src/codepilot/api/static/assets/"
  [ -n "$(ls -A "$DEST/src/codepilot/api/static/assets/")" ] || die "static/assets/ 为空"

  python3 -m compileall -q "$DEST/src/codepilot/api/app.py" || die "发布仓 app.py 编译失败"
  python3 -m compileall -q "$BACKEND/src/codepilot/api/app.py" || die "源码 app.py 编译失败"
  (cd "$DEST" && git diff --check) || die "git diff --check 发现问题（见上方输出）"

  local yaml="$DEST/.catpaw/catpaw_deploy.yaml"
  [ -f "$yaml" ] || die "缺少 $yaml"
  grep -q '^type: cloudnative' "$yaml" || die "部署配置 type 不是 cloudnative"
  grep -Eq '^python: *"3\.12"' "$yaml" || die "部署配置 python 不是 3.12"
  # repo/branch 是 CloudNative 构建的 checkout 来源（2026-09-03 实测：字段缺失、或用
  # HTTPS 地址会 502，均导致 checkout 阶段 git exit 128）。必须精确指向发布仓自身；
  # build.tools 允许存在（为纯 wheel 依赖之外的情况兜底）。
  grep -q '^repo: ssh://git@git.sankuai.com/~jiangwenzhe02/jingwai-agent-main.git$' "$yaml" \
    || die "部署配置 repo 字段缺失或不正确（应为发布仓的 SSH 地址，不可用 HTTPS）"
  grep -q '^branch: master$' "$yaml" || die "部署配置 branch 不是 master"
  grep -q 'python server.py' "$yaml" || die "runCmd 缺少 python server.py"

  # 平台从发布仓根 manifest.yaml 读取构建工具链（2026-09-08 实测：仅在
  # .catpaw/catpaw_deploy.yaml 声明 gcc-c++ 时容器内仍无 C++ 编译器，
  # contourpy 回退 sdist 编译报 meson Unknown compiler）。
  local manifest="$DEST/manifest.yaml"
  [ -f "$manifest" ] || die "缺少 $manifest（平台按它安装构建工具链）"
  grep -q 'gcc-c++' "$manifest" || die "manifest.yaml 未声明 gcc-c++（CentOS 7 默认无 C++ 编译器）"
  grep -q 'python server.py' "$manifest" || die "manifest.yaml 缺少 python server.py"
  awk '/^ports:/{getline; print; exit}' "$yaml" | grep -Eq '^[[:space:]]*- 8000[[:space:]]*$' \
    || die "ports 首个端口不是 8000"
  if sed -n '/^cmd:/,/^target:/p' "$yaml" | grep -q 'npm'; then
    die "部署配置 cmd 中出现 npm（CloudNative 构建镜像没有 npm）"
  fi
  log "校验通过：静态产物 / Python 编译 / git diff --check / 部署配置"
}

# ---------- 4. 提交推送 ----------
commit_push() {
  if [ -z "$(git -C "$DEST" status --porcelain)" ]; then
    log "发布仓无改动，不创建空提交，继续部署现有 $BRANCH"
    COMMIT_SHA="$(git -C "$DEST" rev-parse --short HEAD)"
    return 0
  fi
  local msg
  msg="${MSG:-chore: sync and deploy ($(date '+%Y-%m-%d %H:%M'))}"
  git -C "$DEST" add -A
  # .env 被发布仓 .gitignore 排除，但个人测试仓需要它随仓进入容器
  #（CloudNative 无环境变量注入机制），因此强制纳入版本控制。
  if [ -f "$DEST/.env" ]; then
    git -C "$DEST" add -f .env
  fi
  git -C "$DEST" commit -m "$msg" >/dev/null
  git -C "$DEST" push origin "$BRANCH" || die "git push 失败"
  COMMIT_SHA="$(git -C "$DEST" rev-parse --short HEAD)"
  log "已提交并推送：$COMMIT_SHA  $msg"
}

# ---------- 5. 部署触发 ----------
trigger_deploy() {
  if [ -n "${DEPLOY_TRIGGER_CMD:-}" ]; then
    log "触发 CloudNative 部署：$DEPLOY_TRIGGER_CMD"
    bash -c "$DEPLOY_TRIGGER_CMD" || die "部署触发命令失败"
  else
    warn "本机无部署 CLI：请现在用 CatPaw（catpaw_deploy 工具）对发布仓触发 CloudNative 部署"
    warn "脚本将持续轮询切流状态（最长 $((TOTAL_WAIT / 60)) 分钟），部署触发后自动完成验收"
  fi
}

# ---------- 6. 切流验收 ----------
load_expected_refs() {
  local html="$DEST/src/codepilot/api/static/index.html"
  [ -f "$html" ] || die "缺少 $html，无法确定期望资源"
  REPO_REFS="$(grep -o 'assets/index-[^"]*' "$html" | sort -u | tr '\n' ' ' | sed 's/ $//')"
  [ -n "$REPO_REFS" ] || die "发布仓 index.html 未引用任何 assets 资源"
}

# 解析 /api/health 的 JSON 字段（无 jq 环境，用 python3）
health_field() {
  printf '%s' "$1" | python3 -c \
    "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('$2',''))
except Exception:
    pass" 2>/dev/null || true
}

# 切流是否完成：健康 + 新实例已上线 + 线上引用与发布仓一致 + 新资源 200。
# 纯后端部署（前端 hash 未变）靠 started_at 判断新实例是否已替换。
cutover_ok() {
  local bust="?_t=$(date +%s)"
  local health body live_refs ref code started
  health="$(curl -fsS --max-time 10 "$SITE/api/health" 2>/dev/null || true)"
  [ "$(health_field "$health" status)" = "ok" ] || return 1
  started="$(health_field "$health" started_at_epoch)"
  if [ "$PUSH_EPOCH" -gt 0 ]; then
    # 新版本实例必须携带 started_at（旧实例无此字段视为未切流），
    # 且启动时间晚于推送时刻（2 分钟容差覆盖平台时钟偏移）
    [ -n "$started" ] || return 1
    [ "$started" -ge $((PUSH_EPOCH - 120)) ] || return 1
  fi
  body="$(curl -fsS --max-time 10 "$SITE/$bust" 2>/dev/null || true)"
  [ -n "$body" ] || return 1
  case "$body" in *"frontend build is unavailable"*) return 1 ;; esac
  live_refs="$(printf '%s' "$body" | grep -o 'assets/index-[^"]*' | sort -u | tr '\n' ' ' | sed 's/ $//')"
  [ -n "$live_refs" ] || return 1
  [ "$live_refs" = "$REPO_REFS" ] || return 1
  for ref in $REPO_REFS; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SITE/$ref$bust" || true)"
    [ "$code" = "200" ] || return 1
  done
  return 0
}

live_refs_summary() {
  local refs
  refs="$(curl -fsS --max-time 10 "$SITE/?_t=$(date +%s)" 2>/dev/null \
    | grep -o 'assets/index-[^"]*' | sort -u | tr '\n' ' ' | sed 's/ $//' || true)"
  echo "${refs:-(无响应/未就绪)}"
}

wait_cutover() {
  local waited=0 retried=0
  while :; do
    if cutover_ok; then
      log "切流完成：线上资源与发布仓一致（$REPO_REFS），实例已更新"
      return 0
    fi
    if [ "$waited" -ge "$TOTAL_WAIT" ]; then
      die "等待 $((TOTAL_WAIT / 60)) 分钟仍未切流，当前线上引用：$(live_refs_summary)
按 DEPLOY.md 失败处理：在同一 $BRANCH 提交上重新触发一次部署，然后运行 deploy.sh verify 复验。"
    fi
    if [ "$retried" -eq 0 ] && [ "$waited" -ge "$RETRY_AFTER" ] && [ -n "${DEPLOY_TRIGGER_CMD:-}" ]; then
      warn "等待 $((RETRY_AFTER / 60)) 分钟未切流，自动重新触发一次部署"
      trigger_deploy
      retried=1
    fi
    printf '  等待切流 %ds/%ds（线上：%s）\n' "$waited" "$TOTAL_WAIT" "$(live_refs_summary)"
    sleep "$POLL_INTERVAL"
    waited=$((waited + POLL_INTERVAL))
  done
}

# ---------- verify 模式：单次详细验收 ----------
verify_once() {
  load_expected_refs
  local ok=1 bust="?_t=$(date +%s)" health body live_refs ref code started
  health="$(curl -fsS --max-time 10 "$SITE/api/health" 2>/dev/null || true)"
  if [ "$(health_field "$health" status)" = "ok" ]; then
    started="$(health_field "$health" started_at)"
    log "健康检查：status=ok started_at=${started:-（旧版本实例，无此字段）}"
  else
    warn "健康检查失败：${health:-(无响应)}"
    ok=0
  fi
  body="$(curl -fsS --max-time 10 "$SITE/$bust" 2>/dev/null || true)"
  if [ -n "$body" ] && ! printf '%s' "$body" | grep -q 'frontend build is unavailable'; then
    log "根路径：返回 HTML（$(printf '%s' "$body" | wc -c | tr -d ' ') bytes）"
  else
    warn "根路径异常（空响应或 frontend build is unavailable）"
    ok=0
  fi
  live_refs="$(printf '%s' "$body" | grep -o 'assets/index-[^"]*' | sort -u | tr '\n' ' ' | sed 's/ $//' || true)"
  if [ "$live_refs" = "$REPO_REFS" ]; then
    log "切流校验：线上引用与发布仓一致（$REPO_REFS）"
  else
    warn "未切流：线上 = ${live_refs:-（空）}；期望 = $REPO_REFS"
    ok=0
  fi
  for ref in $REPO_REFS; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SITE/$ref$bust" || true)"
    if [ "$code" = "200" ]; then
      log "资源可访问：$ref -> 200"
    else
      warn "资源不可访问：$ref -> ${code:-(无响应)}"
      ok=0
    fi
  done
  if [ "$ok" -eq 1 ]; then
    log "验收通过：$SITE"
  else
    die "线上验收未通过（见上方 warn）"
  fi
}

# ---------- 主流程 ----------
main() {
  log "模式：$MODE"
  case "$MODE" in
    verify)
      verify_once
      ;;
    dry-run | full)
      clean_junk
      precheck
      sync_all
      validate
      if [ "$MODE" = "dry-run" ]; then
        local st
        st="$(git -C "$DEST" status --short || true)"
        if [ -n "$st" ]; then
          log "dry-run 预演通过。同步已在发布仓工作区产生如下待提交改动（未提交）："
          printf '%s\n' "$st"
          warn "这些改动会在下次正式部署时一并提交推送；如需丢弃：git -C 发布仓 reset --hard HEAD && git -C 发布仓 clean -fd <受影响目录>"
        else
          log "dry-run 预演通过。发布仓无待提交改动"
        fi
        log "dry-run 结束：未提交、未推送、未部署"
        exit 0
      fi
      commit_push
      load_expected_refs
      PUSH_EPOCH=$(date +%s)
      trigger_deploy
      wait_cutover
      log "部署完成"
      log "  发布提交：$COMMIT_SHA（$BRANCH）"
      log "  测试站点：$SITE"
      log "  健康检查：$(curl -fsS --max-time 10 "$SITE/api/health" 2>/dev/null || echo '失败')"
      ;;
    *)
      die "未知模式：$MODE"
      ;;
  esac
}

main
