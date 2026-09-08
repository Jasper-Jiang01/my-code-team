#!/usr/bin/env bash
# baa-basic 环境检测脚本（macOS / Linux）
# 零依赖：仅用系统自带 bash，不需要 python/node 即可运行。
#
# 职责：仅检测 python3 与 node(>=14) 是否就绪。缺失时输出 READY=false 与缺失项，
#       不再自动安装——由上层 Agent 按所在 agent 环境（catdesk / 其他）引导用户安装。
#       详见 references/environment-setup.md。
#
# 输出契约（写 stdout，供上层 Agent 解析；人类可读提示走 stderr）：
#   BAA_BOOTSTRAP_BEGIN
#   OS=macos|linux
#   PYTHON_CMD=python3            # 空=缺失
#   PYTHON_VERSION=3.11.5         # 空=未知
#   NODE_OK=true|false
#   NODE_VERSION=v18.17.0         # 空=未知
#   READY=true|false             # python 与 node 均就绪
#   MANUAL_ACTIONS=Missing Python 3;Missing Node.js(>=14)   # 缺失项描述，可能为空
#   BAA_BOOTSTRAP_END
#
# 退出码：
#   0  READY（python + node 均就绪）
#   10 缺 python
#   11 缺 node
#   12 python 与 node 均缺失

# 尽量保证自身输出为 UTF-8（避免中文提示乱码）。
# 仅在完全没有 locale 设置时才兜底，避免覆盖系统已有的正常 UTF-8 locale
# （如 macOS 的 en_US.UTF-8；强设 C.UTF-8 在 macOS 上不受支持反而致乱码）。
if [ -z "$LANG" ] && [ -z "$LC_ALL" ]; then
  if locale -a 2>/dev/null | grep -qi '^C\.UTF-\?8$'; then
    export LANG="C.UTF-8"
  elif locale -a 2>/dev/null | grep -qi '^en_US\.UTF-\?8$'; then
    export LANG="en_US.UTF-8"
  fi
fi

NODE_MIN_MAJOR=14

# 纯静态文本用 log；含变量的用 logf（printf 格式化）。
# ⚠️ macOS 自带 bash 3.2 有 bug：双引号字符串内同时含多字节字符(中文/emoji)
#    与 inline 变量展开($x)时，变量值会被吞掉。必须把变量作为 printf 独立参数传入。
log()  { printf '%s\n' "$1" >&2; }
logf() { local _fmt="$1"; shift; printf "$_fmt"'\n' "$@" >&2; }

# ─── 判定 OS ────────────────────────────────────────────
detect_os() {
  case "$(uname -s)" in
    Darwin*) echo "macos" ;;
    Linux*)  echo "linux" ;;
    *)       echo "unknown" ;;
  esac
}

OS="$(detect_os)"

# ─── 检测 python3 ───────────────────────────────────────
PYTHON_CMD=""
PYTHON_VERSION=""
detect_python() {
  local cmd
  for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
      # 确认是 python3.x
      local ver
      ver="$("$cmd" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null)"
      if [ -n "$ver" ] && [ "${ver%%.*}" = "3" ]; then
        PYTHON_CMD="$cmd"
        PYTHON_VERSION="$ver"
        return 0
      fi
    fi
  done
  return 1
}

# ─── 检测 node(>=NODE_MIN_MAJOR) ────────────────────────
NODE_OK="false"
NODE_VERSION=""
detect_node() {
  if command -v node >/dev/null 2>&1; then
    NODE_VERSION="$(node -v 2>/dev/null)"        # 形如 v18.17.0
    local major="${NODE_VERSION#v}"; major="${major%%.*}"
    if [ -n "$major" ] && [ "$major" -ge "$NODE_MIN_MAJOR" ] 2>/dev/null; then
      NODE_OK="true"
      return 0
    fi
  fi
  return 1
}

# ─── 执行 ───────────────────────────────────────────────
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
logf "🔍 baa-basic 环境检测（OS=%s）" "$OS"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

py_ok=0; node_ok=0
MISSING=""
if detect_python; then
  py_ok=1
else
  log "❌ 未检测到 python3"
  MISSING="Missing Python 3"
fi
if detect_node; then
  node_ok=1
else
  logf "❌ 未检测到 node 或版本低于 %s" "$NODE_MIN_MAJOR"
  MISSING="${MISSING:+$MISSING;}Missing Node.js(>=14)"
fi

READY="false"
[ "$py_ok" = 1 ] && [ "$node_ok" = 1 ] && READY="true"

# ─── 输出契约块 ─────────────────────────────────────────
printf 'BAA_BOOTSTRAP_BEGIN\n'
printf 'OS=%s\n' "$OS"
printf 'PYTHON_CMD=%s\n' "$PYTHON_CMD"
printf 'PYTHON_VERSION=%s\n' "$PYTHON_VERSION"
printf 'NODE_OK=%s\n' "$NODE_OK"
printf 'NODE_VERSION=%s\n' "$NODE_VERSION"
printf 'READY=%s\n' "$READY"
printf 'MANUAL_ACTIONS=%s\n' "$MISSING"
printf 'BAA_BOOTSTRAP_END\n'

# ─── 退出码 ─────────────────────────────────────────────
if [ "$READY" = "true" ]; then
  exit 0
fi
if [ "$py_ok" = 0 ] && [ "$node_ok" = 0 ]; then exit 12; fi
if [ "$py_ok" = 0 ]; then exit 10; fi
exit 11
