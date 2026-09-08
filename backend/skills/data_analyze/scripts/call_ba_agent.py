#!/usr/bin/env python3
"""
BA-Agent API 调用脚本
支持：分析模式、规划模式、文件上传、分享链接、分享至学城、项目列表、对话历史
"""

import json
import os
import sys
import argparse
import subprocess
import tempfile
import time

# ─── 跨平台编码兜底 ───────────────────────────────────────
# Windows 控制台默认 cp936(GBK)，直接 print emoji/中文会乱码或抛
# UnicodeEncodeError。统一把 stdout/stderr 重配为 utf-8。
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # Python < 3.7 兜底
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────
# lx-cli 灵犀埋点（step 上报）
# ─────────────────────────────────────────────

def _lx_step(step_id, step_name, is_core=False, **kwargs):
    """调用 lx.js 上报 step 埋点，失败时静默，不影响业务逻辑"""
    try:
        lx_path = os.path.join(SCRIPT_DIR, 'lx.js')
        if not os.path.exists(lx_path):
            return
        args = ['node', lx_path, 'skill', 'step', '--step-id', step_id]
        for k, v in kwargs.items():
            args.extend(['--custom', f'{k}={v}'])
        subprocess.run(args, capture_output=True, timeout=5)
    except Exception:
        pass


def _run_auth_script(args, env):
    """
    运行某一级鉴权子脚本（moa/catdesk/ciba/playwright），并捕获其通过
    BA_AUTH_ERROR_FILE 回传的结构化失败原因（机器可读错误码，如
    'moa_probe_unavailable'）。子进程的 stdout/stderr 仍直接透传到当前
    终端（不使用 capture_output），保证用户能实时看到登录进度/扫码提示。

    返回: (returncode, error_reason)  error_reason 在成功或未回传时为 None
    """
    error_file = None
    try:
        fd, error_file = tempfile.mkstemp(prefix="ba_auth_err_", suffix=".txt")
        os.close(fd)
        run_env = {**env, "BA_AUTH_ERROR_FILE": error_file}
        ret = subprocess.call(args, env=run_env)
        error_reason = None
        try:
            if os.path.exists(error_file):
                content = open(error_file, "r", encoding="utf-8").read().strip()
                if content:
                    error_reason = content
        except Exception:
            pass
        return ret, error_reason
    finally:
        if error_file:
            try:
                os.remove(error_file)
            except Exception:
                pass


# 收集本次运行中所有因 Chromium 未安装而跳过的图表渲染提示
# chart_to_markdown 发现 ChromiumNotFoundError 时往这里 append，
# cmd_analyze / cmd_plan 最终输出 JSON 时一并写入 chromium_hint 字段
_chromium_hints: list = []
# 环境域名映射
# test/测试：https://ba-ai.bi.test.sankuai.com
# st/staging/预上线：https://ba-ai.bi.st.sankuai.com
# prod/线上（默认）：https://ba-ai.sankuai.com
_ENV_URL_MAP = {
    "prod":    "https://ba-ai.sankuai.com",
    "st":      "https://ba-ai.bi.st.sankuai.com",
    "staging": "https://ba-ai.bi.st.sankuai.com",
}
_env = os.environ.get("BA_ENV", "prod").lower()
BA_BASE_URL = os.environ.get("BA_BASE_URL") or _ENV_URL_MAP.get(_env, _ENV_URL_MAP["prod"])


def _default_data_dir(env: str) -> str:
    """按环境返回默认的运行时状态目录（token/cookie 隔离存储）。
    Windows → %LOCALAPPDATA%\\ba-analysis（符合系统惯例）
    其他    → ~/.cache/ba-analysis
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ba-analysis")
    return os.path.expanduser("~/.cache/ba-analysis")


# 运行时状态文件目录（cookies、token、misId），按环境隔离
# 优先用环境变量 BA_DATA_DIR；未指定时由 _default_data_dir 按 BA_ENV 决定
BA_DATA_DIR = os.environ.get("BA_DATA_DIR") or _default_data_dir(_env)
os.makedirs(BA_DATA_DIR, exist_ok=True)

TOKEN_FILE = os.path.join(BA_DATA_DIR, "access_token.txt")

# 禁用自动重鉴权开关（子 agent 调用时通过 --no-reauth 参数或 BA_NO_REAUTH=1 环境变量启用）
# 启用后，遇到 token 过期直接以退出码 2 报错，不触发 CIBA 流程
_NO_REAUTH = os.environ.get("BA_NO_REAUTH", "0") == "1"

# 鉴权顺序按 OS 定制：
#   Linux/Ubuntu  → MOA → CIBA → Playwright
#   macOS/Windows → MOA → Playwright（跳过 CIBA，其在 mac/win 有已知问题）
_SKIP_CIBA = sys.platform in ("darwin", "win32")


def _is_catpaw_agent() -> bool:
    """判定当前是否运行在 catpaw agent 环境。

    catpaw agent 下 CatDesk 内置浏览器可复用 SSO 登录态，鉴权顺序应为
    MOA → CatDesk（跳过 CIBA/Playwright）。判定优先级：
      1. 显式环境变量 BA_AGENT=catpaw（最可靠，由 catpaw 运行时注入）
      2. 兜底：探测 catdesk CLI 是否可用（仅 catpaw 环境安装该 CLI）
    """
    if os.environ.get("BA_AGENT", "").strip().lower() == "catpaw":
        return True
    # 兜底探测：catdesk --version 成功即认为处于 catpaw 环境
    _cmds = ["catdesk.cmd", "catdesk"] if sys.platform == "win32" else ["catdesk"]
    for _c in _cmds:
        try:
            if subprocess.run([_c, "--version"], capture_output=True,
                              shell=False).returncode == 0:
                return True
        except (FileNotFoundError, OSError):
            continue
    return False


_IS_CATPAW = _is_catpaw_agent()

# 灰度链路配置（通过 --gray-release 参数或环境变量 BA_GRAY_RELEASE 设置）
# 设置后，所有请求 Header 中会自动添加 gray-release-set: <value>
_GRAY_RELEASE_SET = os.environ.get("BA_GRAY_RELEASE", "").strip() or None

# BA-Agent client_id，用于构造 cookie key（{client_id}_sso）
_BA_CLIENT_ID = "07b8be90c9"


# ─────────────────────────────────────────────
# Cookie 构造
# ─────────────────────────────────────────────

def _build_cookie_header(token: str) -> str:
    """
    构造请求 Cookie 字符串，包含两个字段：
      1. {client_id}_sso  = access_token 值
      2. misId            = 当前登录用户的 misId（从缓存文件读取）
    """
    parts = [f"{_BA_CLIENT_ID}_ssoid={token}"]
    # 读取 misId
    mis_id = None
    global BA_DATA_DIR
    mis_id_file = os.path.join(BA_DATA_DIR, "mis_id.txt")
    if os.path.exists(mis_id_file):
        try:
            mis_id = open(mis_id_file).read().strip() or None
        except Exception:
            pass
    if not mis_id:
        mis_id = os.environ.get("MIS_ID") or os.environ.get("MIS_ID_HINT")
    if mis_id:
        parts.append(f"misId={mis_id}")
    return "; ".join(parts)


# ─────────────────────────────────────────────
# 鉴权
# ─────────────────────────────────────────────

def get_access_token():
    global TOKEN_FILE
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            token = f.read().strip()
        if token:
            return token
    return None


def _read_mis_id():
    """从环境变量或 mis_id.txt 读取 misId，用于自动重鉴权"""
    mis = os.environ.get("MIS_ID") or os.environ.get("MIS_ID_HINT") or os.environ.get("SANDBOX_MIS")
    if mis:
        return mis
    # 读取与 token 同目录的 mis_id.txt（由 inject-ba-cookie-supabase-ciba.py 写入，子 agent 沙箱可读）
    global BA_DATA_DIR
    mis_id_file = os.path.join(BA_DATA_DIR, "mis_id.txt")
    if os.path.exists(mis_id_file):
        try:
            with open(mis_id_file) as f:
                mis = f.read().strip()
            if mis:
                return mis
        except Exception:
            pass
    return None


def _reauth(reason="token 已过期或无效"):
    """触发 SSO 重鉴权（三级降级：MOA → CIBA → Playwright 浏览器登录）"""
    if _NO_REAUTH:
        print(f"❌ {reason}，自动重鉴权已禁用（--no-reauth 模式），请由主 agent 完成鉴权后重试", file=sys.stderr)
        # 埋点：鉴权最终失败（终态汇总）——子 agent 场景下被 --no-reauth 拦截，未实际发起降级链路
        _lx_step('auth-failed', '鉴权最终失败', fail_reason='no_reauth_disabled', reason=reason)
        sys.exit(2)
    mis_id = _read_mis_id()
    print(f"⚠️  {reason}，正在自动触发 SSO 重鉴权...", file=sys.stderr, flush=True)
    _env_for_reauth = os.environ.get("BA_ENV", "prod")
    _reauth_env = {**os.environ, "BA_ENV": _env_for_reauth, "BA_DATA_DIR": BA_DATA_DIR}

    # 埋点：鉴权流程开始
    _lx_step('auth-start', '鉴权流程开始', reason=reason)

    # 步骤 A：尝试 MOA
    print("🔑 尝试 MOA 本地鉴权...", file=sys.stderr)
    ret, moa_error_reason = _run_auth_script(
        [sys.executable, os.path.join(SCRIPT_DIR, "inject-ba-cookie-moa.py")],
        _reauth_env,
    )
    if ret == 0:
        new_token = get_access_token()
        if new_token:
            print("✅ MOA 鉴权成功，自动重试请求...", file=sys.stderr, flush=True)
            _lx_step('auth-moa.success', 'MOA 鉴权成功')
            return new_token

    # MOA 失败（error_reason 来自 inject-ba-cookie-moa.py 回传的具体错误码，
    # 如 moa_npx_not_found / moa_tool_not_installed / moa_probe_unavailable /
    # moa_exchange_failed / moa_token_extract_failed；未回传时兜底为 unknown）
    moa_error_reason = moa_error_reason or "unknown"
    _lx_step('auth-moa.failure', 'MOA 鉴权失败', error_reason=moa_error_reason)

    # 步骤 B'：catpaw agent 专用降级 —— CatDesk 浏览器鉴权
    # catpaw 环境下鉴权顺序为 MOA → CatDesk，跳过 CIBA / Playwright。
    if _IS_CATPAW:
        print("🔑 MOA 失败，当前为 catpaw agent 环境，尝试 CatDesk 浏览器鉴权...", file=sys.stderr)
        _lx_step('auth-catdesk.start', 'catpaw 环境降级 CatDesk')
        catdesk_args = [sys.executable, os.path.join(SCRIPT_DIR, "inject-ba-cookie-catdesk.py")]
        if mis_id:
            catdesk_args.append(mis_id)
        catdesk_args.extend(["--env", _env_for_reauth])
        ret, catdesk_error_reason = _run_auth_script(catdesk_args, _reauth_env)
        if ret == 0:
            new_token = get_access_token()
            if new_token:
                print("✅ CatDesk 鉴权成功，自动重试请求...", file=sys.stderr, flush=True)
                _lx_step('auth-catdesk.success', 'CatDesk 鉴权成功')
                return new_token
        # error_reason 如 catdesk_cli_unavailable / catdesk_navigate_failed / catdesk_cookie_not_found
        catdesk_error_reason = catdesk_error_reason or "unknown"
        _lx_step('auth-catdesk.failure', 'CatDesk 鉴权失败', error_reason=catdesk_error_reason)
        # 埋点：鉴权最终失败（终态汇总）——catpaw 环境下 MOA + CatDesk 均失败，不再降级
        # error_reason 取最后一级（CatDesk）的具体失败原因，moa_error_reason 附带记录前一级原因供交叉分析
        _lx_step('auth-failed', '鉴权最终失败', fail_reason='catdesk_failed',
                 last_method='catdesk', error_reason=catdesk_error_reason, moa_error_reason=moa_error_reason)
        print("❌ CatDesk 鉴权失败，请在 CatDesk 中登录美团 SSO 后重试，或联系 BA-Agent 系统管理员", file=sys.stderr)
        sys.exit(2)

    # 步骤 B：降级 CIBA（仅 Linux；mac/win 跳过，CIBA 在这两个平台有已知问题）
    ciba_error_reason = None
    if _SKIP_CIBA:
        print("⏭️  当前系统（macOS/Windows）跳过 CIBA，直接尝试 Playwright 浏览器登录...", file=sys.stderr)
        _lx_step('auth-ciba.skipped', 'macOS/Windows 跳过 CIBA')
        ciba_error_reason = "skipped_mac_win"
    elif mis_id:
        print("🔑 MOA 失败，尝试 CIBA 鉴权...", file=sys.stderr)
        ret, ciba_error_reason = _run_auth_script(
            [sys.executable, os.path.join(SCRIPT_DIR, "inject-ba-cookie-supabase-ciba.py"), mis_id],
            _reauth_env,
        )
        if ret == 0:
            new_token = get_access_token()
            if new_token:
                print("✅ CIBA 鉴权成功，自动重试请求...", file=sys.stderr, flush=True)
                _lx_step('auth-ciba.success', 'CIBA 鉴权成功')
                return new_token
        # error_reason 如 ciba_identifier_missing / ciba_auth_request_failed /
        # ciba_confirm_timeout / ciba_exchange_failed / ciba_token_extract_failed
        ciba_error_reason = ciba_error_reason or "unknown"
        _lx_step('auth-ciba.failure', 'CIBA 鉴权失败，降级 Playwright', error_reason=ciba_error_reason)
    else:
        ciba_error_reason = "no_mis_id"

    # 步骤 C：降级 Playwright 浏览器登录
    print("🔑 CIBA 失败，尝试 Playwright 浏览器登录...", file=sys.stderr)
    playwright_args = [sys.executable, os.path.join(SCRIPT_DIR, "inject-ba-cookie-playwright.py")]
    if mis_id:
        playwright_args.append(mis_id)
    playwright_args.extend(["--env", _env_for_reauth])
    ret, playwright_error_reason = _run_auth_script(playwright_args, _reauth_env)
    if ret == 0:
        new_token = get_access_token()
        if new_token:
            print("✅ Playwright 鉴权成功，自动重试请求...", file=sys.stderr, flush=True)
            _lx_step('auth-playwright.success', 'Playwright 鉴权成功')
            return new_token

    # error_reason 如 playwright_pkg_install_failed / playwright_no_browser /
    # playwright_login_timeout / playwright_user_interrupted / playwright_exception
    playwright_error_reason = playwright_error_reason or "unknown"
    _lx_step('auth-playwright.failure', 'Playwright 鉴权失败', error_reason=playwright_error_reason)
    # 埋点：鉴权最终失败（终态汇总）——MOA/CIBA/Playwright 三级降级全部失败
    # error_reason 取最后一级（Playwright）的具体失败原因，同时附带 moa/ciba 的原因供交叉分析根因
    _lx_step('auth-failed', '鉴权最终失败', fail_reason='all_methods_failed', last_method='playwright',
             error_reason=playwright_error_reason, moa_error_reason=moa_error_reason,
             ciba_error_reason=ciba_error_reason)
    print("❌ 三种鉴权方式均失败，请联系 BA-Agent 系统管理员", file=sys.stderr)
    sys.exit(2)


def check_auth_or_exit():
    token = get_access_token()
    if not token:
        token = _reauth("首次使用，尚未鉴权")
    return token


def _is_expired_response(resp, is_stream=False):
    """判断响应是否为 token 过期：302 重定向、401/403、或 200 但 body 为空。
    流式请求（is_stream=True）下跳过"body 为空"的判断，避免因连接中断误报 token 过期。"""
    if resp.status_code in (301, 302, 307, 308):
        return True
    if resp.status_code in (401, 403):
        return True
    # 流式请求不能用 resp.text（body 可能合法为空或未读完），跳过此判断
    if not is_stream and resp.status_code == 200 and not resp.text.strip():
        return True
    return False


# ─────────────────────────────────────────────
# HTTP 工具
# ─────────────────────────────────────────────

def _ensure_requests():
    try:
        import requests
        return requests
    except ImportError:
        pass
    # 用当前解释器的 pip 模块安装（避免 pip3 命令在 Windows 不存在）；
    # externally-managed 环境（如 uv/PEP 668）普通安装会被拒，追加 --break-system-packages 兜底
    for extra in ([], ["--break-system-packages"]):
        try:
            ret = subprocess.call([sys.executable, "-m", "pip", "install", "-q", "requests"] + extra)
        except Exception:
            continue
        if ret == 0:
            try:
                import requests
                return requests
            except ImportError:
                continue
    raise RuntimeError(
        f"自动安装 requests 失败，请手动执行：{sys.executable} -m pip install requests "
        "(externally-managed 环境如 uv/PEP668 追加 --break-system-packages)"
    )


def http_get(path, params=None, token=None, referer=None, timeout=30):
    requests = _ensure_requests()
    token = token or check_auth_or_exit()
    for attempt in range(2):
        headers = {
            "access-token": token,
            "Content-Type": "application/json",
            "X-Source": "catclaw",
            "Cookie": _build_cookie_header(token),
        }
        if referer:
            headers["referer"] = referer
        if _GRAY_RELEASE_SET:
            headers["gray-release-set"] = _GRAY_RELEASE_SET
        resp = requests.get(BA_BASE_URL + path, params=params, headers=headers,
                            timeout=timeout, allow_redirects=False)
        if _is_expired_response(resp):
            if attempt == 0:
                token = _reauth()
                continue
            print("❌ 重鉴权后请求仍失败", file=sys.stderr)
            sys.exit(2)
        try:
            return resp.json()
        except Exception:
            print(f"❌ 响应解析失败（HTTP {resp.status_code}），服务端返回非 JSON 内容：{resp.text[:200]}", file=sys.stderr)
            sys.exit(1)


def http_post(path, data=None, token=None):
    requests = _ensure_requests()
    token = token or check_auth_or_exit()
    for attempt in range(2):
        headers = {
            "access-token": token,
            "Content-Type": "application/json",
            "X-Source": "catclaw",
            "Cookie": _build_cookie_header(token),
        }
        if _GRAY_RELEASE_SET:
            headers["gray-release-set"] = _GRAY_RELEASE_SET
        resp = requests.post(BA_BASE_URL + path, json=data, headers=headers,
                             timeout=60, allow_redirects=False)
        if _is_expired_response(resp):
            if attempt == 0:
                token = _reauth()
                continue
            print("❌ 重鉴权后请求仍失败", file=sys.stderr)
            sys.exit(2)
        try:
            return resp.json()
        except Exception:
            print(f"❌ 响应解析失败（HTTP {resp.status_code}），服务端返回非 JSON 内容：{resp.text[:200]}", file=sys.stderr)
            sys.exit(1)


def http_post_stream(path, data=None, token=None):
    """SSE 流式请求，yield (event_type, data_str)；token 过期时自动重鉴权重试一次"""
    requests = _ensure_requests()
    token = token or check_auth_or_exit()
    for attempt in range(2):
        headers = {
            "access-token": token,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Source": "catclaw",
            "Cookie": _build_cookie_header(token),
        }
        if _GRAY_RELEASE_SET:
            headers["gray-release-set"] = _GRAY_RELEASE_SET
        with requests.post(BA_BASE_URL + path, json=data, headers=headers,
                           stream=True, timeout=1800, allow_redirects=False) as resp:
            if _is_expired_response(resp, is_stream=True):
                if attempt == 0:
                    token = _reauth()
                    continue
                print("❌ 重鉴权后流式请求仍失败", file=sys.stderr)
                sys.exit(2)
            resp.encoding = "utf-8"
            # 按 SSE 标准：event: 和 data: 属于同一消息块，用空行分隔。
            # 这里收集完整消息块后再 dispatch，避免逐行处理时 current_event 被过早清空。
            current_event = None
            current_data_lines = []
            try:
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if raw_line is None:
                        continue
                    if raw_line == "":
                        # 空行：当前消息块结束，dispatch 并重置
                        if current_data_lines:
                            data_str = "\n".join(current_data_lines)
                            yield current_event, data_str
                        current_event = None
                        current_data_lines = []
                        continue
                    if raw_line.startswith("event:"):
                        current_event = raw_line[len("event:"):].strip()
                    elif raw_line.startswith("data:"):
                        current_data_lines.append(raw_line[len("data:"):].strip())
                # 流结束时若还有未 dispatch 的数据块，也要处理
                if current_data_lines:
                    data_str = "\n".join(current_data_lines)
                    yield current_event, data_str
            except Exception as stream_err:
                # 捕获连接中断（如进程被 SIGTERM、网络抖动等），与 token 过期无关
                print(f"⚠️ 流式请求被中断（非 token 问题，可重试）: {stream_err}", file=sys.stderr)
                return
        return  # 正常结束，跳出重试循环


# ─────────────────────────────────────────────
# 核心功能
# ─────────────────────────────────────────────

def upload_file(file_path, project_id=None, display_name=None):
    """上传文件到 BA-Agent。display_name 指定上传时的文件名（如用户原始文件名），
    默认使用本地文件的 basename。"""
    requests = _ensure_requests()
    token = check_auth_or_exit()
    url = BA_BASE_URL + "/baagent/api/v2/platform/uploadFile"
    for attempt in range(2):
        headers = {
            "access-token": token,
            "X-Source": "catclaw",
            "Cookie": _build_cookie_header(token),
        }
        if _GRAY_RELEASE_SET:
            headers["gray-release-set"] = _GRAY_RELEASE_SET
        with open(file_path, "rb") as f:
            fname = display_name if display_name else os.path.basename(file_path)
            import mimetypes as _mt
            _ct = _mt.guess_type(fname)[0] or "application/octet-stream"
            files = {"file": (fname, f, _ct)}
            form_data = {}
            if project_id:
                form_data["projectId"] = project_id
            resp = requests.post(url, headers=headers, files=files, data=form_data,
                                 timeout=120, allow_redirects=False)
        if _is_expired_response(resp):
            if attempt == 0:
                token = _reauth()
                continue
            print("❌ 重鉴权后文件上传仍失败", file=sys.stderr)
            sys.exit(2)
        result = resp.json()
        if result.get("code") != 0:
            print(f"❌ 文件上传失败: {result.get('msg')}")
            sys.exit(1)
        return result["data"]


def create_conversation(name, mode="common", project_id=None, ctx_override=None, skill_enabled=0):
    """
    创建会话。
    ctx_override：直接传入外部 ctx 对象（如来自项目的 ctx），优先级高于 mode 参数构造的默认 ctx。
    skill_enabled：是否开启 deepAgent 模式，1=开启，0=关闭（默认）。
    """
    if ctx_override:
        ctx = ctx_override
        # 若调用者明确指定了 mode，需要同步覆盖 ctx 中的 mode 字段
        # 避免项目 ctx 中的默认 mode("common") 覆盖调用者意图
        ctx["mode"] = mode
        ctx["tab"] = "intelCommon" if mode == "intelCommon" else "common"
        ctx["tabLabel"] = "通用"
        # 过滤 extensions，只保留 id 不为空的项
        if "extensions" in ctx and isinstance(ctx["extensions"], list):
            ctx["extensions"] = [e for e in ctx["extensions"] if e.get("id")]
    else:
        ctx = {
            "mode": mode,
            "tab": "common",
            "tabLabel": "通用",
            "datas": [],
            "extensions": []
        }
    payload = {"name": name, "ctx": ctx, "source": 4, "skillEnabled": skill_enabled}
    if project_id:
        payload["projectId"] = project_id
    result = http_post("/baagent/api/v2/dialogue/create", data=payload)
    if result.get("code") != 0:
        print(f"❌ 创建会话失败: {result.get('msg')}")
        sys.exit(1)
    return result["data"]["id"]


class ChromiumNotFoundError(RuntimeError):
    """Chromium 可执行文件不存在，需要用户确认后才能安装。"""
    pass


def render_html_to_image(html_content, output_path):
    """将 HTML 内容渲染为图片，通过独立子进程运行 Playwright，避免 EPIPE/SIGTERM 污染主进程。
    若 Chromium 不存在，子进程会在 stderr 以 'CHROMIUM_NOT_FOUND' 开头输出错误，
    主进程捕获后抛出 ChromiumNotFoundError，由上层决定是否提示用户安装。
    """
    script = """
import sys, os

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    import subprocess
    for _extra in ([], ["--break-system-packages"]):
        if subprocess.call([sys.executable, "-m", "pip", "install", "-q", "playwright"] + _extra) == 0:
            break
    from playwright.sync_api import sync_playwright

def _launch_browser(p):
    try:
        return p.chromium.launch()
    except Exception as e:
        if "Executable doesn't exist" in str(e) or "executable" in str(e).lower():
            print("CHROMIUM_NOT_FOUND: Chromium 可执行文件不存在，请先安装。", file=sys.stderr)
            sys.exit(2)
        raise

with sync_playwright() as p:
    browser = _launch_browser(p)
    page = browser.new_page(viewport={"width": 800, "height": 450})
    page.set_content(HTML_CONTENT, wait_until="networkidle")
    page.wait_for_timeout(5000)
    page.screenshot(path=OUTPUT_PATH, full_page=True)
    browser.close()
print("OK")
""".replace("HTML_CONTENT", repr(html_content)).replace("OUTPUT_PATH", repr(output_path))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
    )
    stderr_out = result.stderr.strip()
    if result.returncode == 2 or "CHROMIUM_NOT_FOUND" in stderr_out:
        raise ChromiumNotFoundError("Chromium 可执行文件不存在")
    if result.returncode != 0 or "OK" not in result.stdout:
        raise RuntimeError(f"Playwright 子进程失败: {stderr_out[:300]}")
    return output_path


def upload_image_to_s3(local_path, chart_name="chart", token=None):
    """将本地图片通过 BA-Agent uploadImage 接口上传，返回 (url, error_msg)。
    失败最多重试一次（共 2 次），两次都失败返回 (None, 错误信息字符串)。"""
    requests = _ensure_requests()
    token = token or get_access_token()
    if not token:
        err = "无 access-token，请先完成 SSO 登录"
        print(f"  ⚠️ 图片上传失败：{err}", file=sys.stderr)
        return None, err

    last_error = None
    for attempt in range(1, 3):
        try:
            with open(local_path, "rb") as f:
                resp = requests.post(
                    BA_BASE_URL + "/baagent/api/v2/sug-manager/uploadImage",
                    headers={
                        "access-token": token,
                        "X-Source": "catclaw",
                        "Cookie": _build_cookie_header(token),
                    },
                    files={"file": (os.path.basename(local_path), f, "image/png")},
                    timeout=60,
                    allow_redirects=False,
                )
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                print(f"  ⚠️ 图片上传第 {attempt} 次失败：{last_error}", file=sys.stderr)
                continue
            result = resp.json()
            if result.get("code") != 0:
                last_error = result.get("msg") or f"code={result.get('code')}"
                print(f"  ⚠️ 图片上传第 {attempt} 次失败：{last_error}", file=sys.stderr)
                continue
            url = result.get("data")
            print(f"  ✅ 图片已上传（第 {attempt} 次）: {url}", file=sys.stderr)
            return url, None
        except Exception as e:
            last_error = str(e)
            print(f"  ⚠️ 图片上传第 {attempt} 次异常：{last_error}", file=sys.stderr)

    print(f"  ❌ 图片上传 2 次均失败，最后错误：{last_error}", file=sys.stderr)
    return None, last_error


def _consume_stream(path, payload, last_content, last_state, ended, plan_block_id, last_chat_response_id):
    """内部函数：消费一次 SSE 流，更新并返回状态元组。
    返回 (last_content, last_state, ended, plan_block_id, last_chat_response_id, interrupted)
    interrupted=True 表示连接中途断开（非正常 end 结束），可尝试 reconnectV2。
    """
    interrupted = False
    for event_type, data_str in http_post_stream(path, data=payload):
        if event_type == "ping":
            continue
        if event_type == "end":
            ended = True
            interrupted = False
            break
        if event_type == "state":
            try:
                last_state = json.loads(data_str)
            except Exception:
                pass
            continue
        if not data_str or data_str == "{}":
            continue
        try:
            msg = json.loads(data_str)
        except Exception:
            continue
        if msg.get("end"):
            ended = True
            interrupted = False
            last_content = msg.get("data", {}).get("content", last_content)
            break
        data_block = msg.get("data", {}) or {}
        # 记录最新的 chatResponseId，供断线重连使用
        msg_id = data_block.get("id")
        if msg_id:
            last_chat_response_id = msg_id
        content = data_block.get("content")
        if content is not None:
            last_content = content
            # 若此消息块包含 plan 类型的 content 片段，记录块顶层 id 作为 chat_response_id
            if msg_id and isinstance(content, list):
                for seg in content:
                    if isinstance(seg, dict) and seg.get("type") == "plan":
                        plan_block_id = msg_id
                        break
    else:
        # for 循环正常耗尽（http_post_stream 因中断提前 return），说明被打断
        if not ended:
            interrupted = True
    return last_content, last_state, ended, plan_block_id, last_chat_response_id, interrupted


def stream_and_collect(path, payload, max_reconnect=3):
    """执行 SSE 流式请求，支持断线自动重连（via reconnectV2）。
    返回 (last_content, last_state, ended, plan_block_id, last_chat_response_id)
    plan_block_id: 包含 plan 类型 content 的消息块顶层 id，用于 confirm_plan 的 chatResponseId
    last_chat_response_id: 流式消息块最新 id，断线时供 reconnect 子命令继续重连
    max_reconnect: 最大重连次数（默认 3 次），避免死循环。
    """
    last_content = None
    last_state = None
    ended = False
    plan_block_id = None
    last_chat_response_id = None
    conversation_id = payload.get("conversationId")

    print("🔄 正在分析中...", file=sys.stderr, flush=True)

    # 首次请求
    last_content, last_state, ended, plan_block_id, last_chat_response_id, interrupted = (
        _consume_stream(path, payload, last_content, last_state, ended, plan_block_id, last_chat_response_id)
    )

    # 断线重连（抽到独立函数 reconnect_stream，reconnect 子命令亦复用）
    if interrupted and not ended:
        last_content, last_state, ended, plan_block_id, last_chat_response_id = (
            reconnect_stream(conversation_id, last_chat_response_id,
                             last_content=last_content, last_state=last_state,
                             ended=ended, plan_block_id=plan_block_id,
                             max_reconnect=max_reconnect)
        )

    return last_content, last_state, ended, plan_block_id, last_chat_response_id


def _latest_chat_response_id_from_history(conversation_id):
    """从 historyListV2 取最新一条 server 消息的 id 作为断线重连的 chatResponseId 兜底。

    流式一条消息都没收到就断时 last_chat_response_id 为空，reconnectV2 无法续传；
    此处从已落盘的历史消息取最新 server 消息 id 作为续传起点。失败返回 None，不影响主流程。
    """
    try:
        token = check_auth_or_exit()
        referer = f"{BA_BASE_URL}/chat/{conversation_id}"
        result = http_get("/baagent/api/v2/chat/historyListV2",
                          params={"conversationId": str(conversation_id), "pageNo": 1, "pageSize": 50},
                          token=token, referer=referer)
        if result.get("code") != 0:
            return None
        messages = (result.get("data") or {}).get("data", []) or []
        for m in reversed(messages):  # 从最新往前找
            if m.get("role") != "server":
                continue
            msg_id = (m.get("id")
                      or m.get("chatResponseId")
                      or (m.get("data") or {}).get("id"))
            if msg_id:
                return str(msg_id)
        return None
    except Exception:
        return None


def reconnect_stream(conversation_id, chat_response_id,
                     last_content=None, last_state=None,
                     ended=False, plan_block_id=None,
                     max_reconnect=3):
    """断线重连：通过 /baagent/api/v2/chat/reconnectV2 续传 SSE 流。
    从外部传入断点前的状态（last_content/last_state/ended/plan_block_id）与起始
    chatResponseId，循环重连直到正常结束或达到 max_reconnect 上限。
    返回 (last_content, last_state, ended, plan_block_id, last_chat_response_id)
    供 stream_and_collect 内部重连与 reconnect 子命令复用。
    """
    last_chat_response_id = chat_response_id
    # 兜底：未提供 chatResponseId（流式一条消息都没收到就断）时，从 history 取最新 server 消息 id
    if not last_chat_response_id and conversation_id:
        last_chat_response_id = _latest_chat_response_id_from_history(conversation_id)
        if last_chat_response_id:
            print(f"🔁 未提供 chatResponseId，已从历史消息取最新 id={last_chat_response_id} 作为重连起点",
                  file=sys.stderr, flush=True)
        else:
            print("⚠️ 重连缺少 chatResponseId，且历史消息无可用 id，无法重连", file=sys.stderr)
            return last_content, last_state, ended, plan_block_id, last_chat_response_id
    interrupted = True  # 进入函数即认为需要重连
    reconnect_count = 0
    print("🔄 正在重连以继续等待分析结果...", file=sys.stderr, flush=True)
    while interrupted and not ended and reconnect_count < max_reconnect:
        if not last_chat_response_id or not conversation_id:
            print("⚠️ 重连缺少 chatResponseId/conversationId，无法继续", file=sys.stderr)
            break
        reconnect_count += 1
        print(
            f"🔁 流式连接中断，尝试重连（第 {reconnect_count}/{max_reconnect} 次），"
            f"chatResponseId={last_chat_response_id}...",
            file=sys.stderr, flush=True
        )
        reconnect_payload = {
            "conversationId": str(conversation_id),
            "chatResponseId": last_chat_response_id,
        }
        last_content, last_state, ended, plan_block_id, last_chat_response_id, interrupted = (
            _consume_stream(
                "/baagent/api/v2/chat/reconnectV2",
                reconnect_payload,
                last_content, last_state, ended, plan_block_id, last_chat_response_id,
            )
        )

    if interrupted and not ended:
        print(
            f"⚠️ 重连 {reconnect_count} 次后仍未完成，返回已收到的部分结果",
            file=sys.stderr
        )

    return last_content, last_state, ended, plan_block_id, last_chat_response_id


def table_to_markdown(table_content):
    if not isinstance(table_content, dict):
        return None
    return chart_to_markdown(table_content)


def chart_to_markdown(chart_content, token=None):
    if not isinstance(chart_content, dict):
        return None
    html_content = chart_content.get("htmlContent", "")
    tabular_data = chart_content.get("tabularData")
    data_markdown = chart_content.get("dataMarkdown")
    chart_name = chart_content.get("name", "图表")
    chart_id = chart_content.get("chartTableId") or chart_content.get("id")

    if not html_content and chart_id and token:
        for attempt in range(1, 3):
            try:
                print(f"  ⏳ 图表 [{chart_name}] 渲染中，第 {attempt} 次请求...", file=sys.stderr, flush=True)
                result = http_get("/baagent/api/v2/report/content",
                                  params={"id": chart_id}, token=token,
                                  timeout=120)
                data = result.get("data", {}) or {}
                status = data.get("status")
                if status == "SUCCESS":
                    html_content = data.get("content", "")
                    print(f"  ✅ 图表 [{chart_name}] 内容就绪", file=sys.stderr)
                    break
                else:
                    error_type = data.get("errorType") or {}
                    error_msg = error_type.get("message") if isinstance(error_type, dict) else str(error_type)
                    print(f"  ⚠️ 图表 [{chart_name}] 第 {attempt} 次返回状态: {status}，错误: {error_msg}", file=sys.stderr)
            except Exception as e:
                print(f"  ⚠️ 图表 [{chart_name}] 第 {attempt} 次请求异常: {e}", file=sys.stderr)
        else:
            print(f"  ❌ 图表 [{chart_name}] 2 次请求均失败，返回获取失败", file=sys.stderr)
            return f"_[图表: {chart_name}（获取失败，请稍后重试）]_"

    if html_content:
        img_path = os.path.join(tempfile.gettempdir(), f"ba_chart_{int(time.time())}.png")
        try:
            render_html_to_image(html_content, img_path)
        except ChromiumNotFoundError:
            # 记录提示到全局列表，等最终输出时一并附上；此处只返回简洁占位
            if not _chromium_hints:
                _chromium_hints.append(
                    "⚠️ **图表截图渲染失败**\n\n"
                    "**原因：** 系统中找不到 Chromium 浏览器，Playwright 无法完成图表截图渲染。\n\n"
                    "**解决方案：** 请在终端执行以下命令安装 Chromium（约 100–200 MB，需要联网）：\n"
                    "```\n"
                    "playwright install chromium\n"
                    "```\n"
                    "安装完成后，告诉我「重新分析」即可。"
                )
            print(f"  ❌ Chromium 未安装，已跳过图表渲染", file=sys.stderr)
            return f"_[图表: {chart_name}（截图渲染失败，Chromium 未安装）]_"
        except Exception as render_err:
            err_msg = f"图表渲染失败：{render_err}"
            print(f"  ❌ {err_msg}", file=sys.stderr)
            return f"_[图表: {chart_name}（{err_msg}）]_"
        s3_url, upload_err = upload_image_to_s3(img_path, chart_name, token=token)
        if s3_url:
            return f"![{chart_name}]({s3_url})"
        else:
            err_detail = upload_err or "未知错误"
            return (
                f"_[图表: {chart_name}（图片上传 S3 失败：{err_detail}，"
                f"本地路径：`{img_path}`）]_"
            )

    if data_markdown:
        return f"**{chart_name}**\n\n{data_markdown}"

    if tabular_data:
        headers = tabular_data.get("headers", [])
        rows_data = tabular_data.get("rows", [])
        if headers:
            lines = [f"**{chart_name}**"]
            lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for row in rows_data:
                cells = [str(c) if c is not None else "" for c in row]
                lines.append("| " + " | ".join(cells) + " |")
            return "\n".join(lines)

    return f"_[图表: {chart_name}（内容加载中）]_"


def extract_summary_segments(content_list):
    """
    在分析模式下，提取 summary 结论区间的 segments。

    优先策略：提取 config.usage == "conclusion" 的所有 segments。
    兜底策略：若无 conclusion 标记，则回退到 streamingEventKey 包含 "summary" 的区间。
    两种策略都找不到时返回 None（调用方降级为全量内容）。
    """
    if not content_list:
        return None

    # 优先策略：config.usage == "conclusion"
    conclusion_segs = [
        seg for seg in content_list
        if isinstance(seg.get("config"), dict) and seg["config"].get("usage") == "conclusion"
    ]
    if conclusion_segs:
        return conclusion_segs

    # 兜底策略：streamingEventKey 包含 "summary" 的区间
    first_summary_idx = None
    last_summary_idx = None

    for i, seg in enumerate(content_list):
        key = seg.get("streamingEventKey") or ""
        if "summary" in key:
            if first_summary_idx is None:
                first_summary_idx = i
            last_summary_idx = i

    if first_summary_idx is None:
        return None

    return content_list[first_summary_idx: last_summary_idx + 1]


def format_content_to_markdown(content_list, token=None):
    parts = []
    for seg in (content_list or []):
        seg_type = seg.get("type", "")
        seg_content = seg.get("content")
        if seg_type in ("markdown", "text"):
            if isinstance(seg_content, str) and seg_content.strip():
                parts.append(seg_content)
        elif seg_type == "table":
            md = table_to_markdown(seg_content)
            if md:
                parts.append(md)
        elif seg_type == "chart":
            md = chart_to_markdown(seg_content, token)
            if md:
                parts.append(md)
        elif seg_type == "file":
            s3_name = None
            if isinstance(seg_content, dict):
                s3_name = seg_content.get("s3Name")
            if s3_name and token:
                result = http_get("/baagent/api/v2/platform/downloadFile",
                                  params={"s3Link": s3_name}, token=token)
                url = result.get("data")
                if url:
                    fname = s3_name.split("/")[-1]
                    parts.append(f"📎 [下载文件: {fname}]({url})")
    return "\n\n".join(parts)


def extract_plan_result(content_list, state, token):
    parts = []
    if state and isinstance(state, dict):
        workspace = state.get("workspace", {})
        for f in workspace.get("file", []):
            if f.get("type") == "result":
                version = f.get("content", {}).get("version", {})
                for item in version.get("contents", []):
                    ct = item.get("contentType", "")
                    d = item.get("data")
                    if ct == "markdown" and isinstance(d, str):
                        parts.append(d)
                    elif ct == "chart":
                        md = chart_to_markdown(d, token)
                        if md:
                            parts.append(md)
                    elif ct == "table":
                        md = table_to_markdown(d)
                        if md:
                            parts.append(md)
            elif f.get("type") == "excel":
                s3_name = f.get("content", {}).get("s3Name")
                if s3_name and token:
                    result = http_get("/baagent/api/v2/platform/downloadFile",
                                      params={"s3Link": s3_name}, token=token)
                    url = result.get("data")
                    if url:
                        fname = s3_name.split("/")[-1]
                        parts.append(f"📊 [下载结果文件: {fname}]({url})")
    if not parts:
        parts.append(format_content_to_markdown(content_list, token))
    return "\n\n".join(filter(None, parts))


# ─────────────────────────────────────────────
# 子命令
# ─────────────────────────────────────────────

def _build_file_list(args, token):
    """
    从 args 中构造 fileList 和 fetchDataRequest，逻辑被 cmd_analyze 和 cmd_plan 共享：
    1. 本地上传文件（--file）
    2. 项目附带文件（--project-id）
    3. 沧澜数据集（--canglan-source-json）→ 同时生成 fetchDataRequest
    4. 学城文档（--km-url）
    返回 (file_list, fetch_data_request)。fetch_data_request 为 None 或 dict。
    """
    import random
    import string

    file_list = []
    fetch_data_request = None

    # 1. 本地文件上传
    if getattr(args, "file", None):
        print(f"📤 上传文件: {args.file}", file=sys.stderr)
        display_name = getattr(args, "display_name", None) or os.path.basename(args.file)
        file_resp = upload_file(args.file, display_name=display_name)
        print(f"✅ 上传成功: {file_resp.get('s3Name')} (文件名: {display_name})", file=sys.stderr)
        file_list.append({
            "originFileName": file_resp.get("originFileName"),
            "s3Name": file_resp.get("s3Name"),
            "dataType": file_resp.get("dataType", "file"),
            "size": file_resp.get("size", ""),
            "hasAuth": True,
        })

    # 2. 项目附带文件
    if getattr(args, "project_id", None):
        project_files = _get_project_files(args.project_id, token=token)
        file_list.extend(project_files)

    # 3. 沧澜数据集（新流程：不预取数据，直接构造 fetchDataRequest 传入 streamV2）
    if getattr(args, "canglan_source_json", None):
        canglan_items = json.loads(args.canglan_source_json)
        fetch_datas = []
        for item in canglan_items:
            # 兼容 project_info sourceList 格式（带 frontExtInfo）和直接格式
            front_ext = item.get("frontExtInfo")
            if front_ext:
                dataset_id = item.get("id")
                dataset_name = item.get("name")
                biz_line_id = front_ext.get("businessLineId")
                metric_list = front_ext.get("metricList")
                subscription_list = front_ext.get("subscriptionInfoList") or []
                subscription_info = subscription_list[0] if subscription_list else {}
                dim_list = subscription_info.get("dimList")
                date_range = front_ext.get("dateRangeOriginalValue") or {"startDate": "NEW", "endDate": "NEW"}
                unique_id = front_ext.get("uniqueId")
                sub = {}
                for k in ("name", "id", "version"):
                    if subscription_info.get(k) is not None:
                        sub[k] = subscription_info[k]
            else:
                dataset_id = item.get("id") or item.get("datasetId")
                dataset_name = item.get("datasetName") or item.get("name")
                biz_line_id = item.get("businessLineId")
                metric_list = item.get("metricList")
                dim_list = item.get("dimList")
                date_range = item.get("dateRange") or {"startDate": "NEW", "endDate": "NEW"}
                unique_id = item.get("uniqueId")
                sub = item.get("subscription") or {}

            if not unique_id:
                rand_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
                unique_id = f"{dataset_id}_{int(time.time() * 1000)}_{rand_str}"

            if not metric_list:
                metric_list = [{"code": "ALL", "name": "全部", "selected": True}]
            if not dim_list:
                dim_list = [{"code": "ALL", "name": "全部", "selected": True}]

            # fileList 条目（简化，无需 s3Name）
            file_list.append({
                "s3Name": None,
                "originFileName": dataset_name,
                "size": None,
                "dataType": "canglan-dataset",
                "previewUrl": None,
                "datasetId": int(dataset_id) if dataset_id else None,
                "metaDataInfo": None,
            })

            # fetchDataRequest.datas 条目
            fetch_entry = {
                "type": "canglan-dataset",
                "id": str(dataset_id),
                "businessLineId": str(biz_line_id) if biz_line_id else None,
                "datasetName": dataset_name,
                "metricList": metric_list,
                "dimList": dim_list,
                "dateRange": date_range,
                "uniqueId": unique_id,
            }
            if sub:
                fetch_entry["subscription"] = sub
            fetch_datas.append(fetch_entry)

        if fetch_datas:
            fetch_data_request = {"datas": fetch_datas}
            print(f"📊 已构造 {len(fetch_datas)} 个沧澜数据集的 fetchDataRequest", file=sys.stderr)

    # 4. 学城文档
    if getattr(args, "km_url", None):
        print(f"📚 解析学城文档: {args.km_url}", file=sys.stderr)
        km_result = http_get("/baagent/api/v2/km/parseKm",
                             params={"url": args.km_url}, token=token)
        if km_result.get("code") == 0 and km_result.get("data", {}).get("success"):
            km_data = km_result["data"]
            file_list.append({
                "originFileName": km_data.get("originFileName"),
                "s3Name": km_data.get("s3Name"),
                "dataType": "km",
                "kmUrl": km_data.get("kmUrl"),
                "kmTitleInfo": km_data.get("kmTitleInfo"),
                "owner": km_data.get("owner"),
                "version": km_data.get("version"),
                "hasAuth": km_data.get("hasAuth", True),
            })
        else:
            print(f"⚠️ 学城文档解析失败: {km_result.get('data', {}).get('errorMsg')}", file=sys.stderr)

    return file_list, fetch_data_request


def cmd_analyze(args):
    token = check_auth_or_exit()
    file_list, fetch_data_request = _build_file_list(args, token)

    # 埋点：分析任务路径入口
    _lx_step('analyze_core-start', '分析任务开始',
             has_file=str(bool(getattr(args, 'file', None))),
             has_km_url=str(bool(getattr(args, 'km_url', None))),
             has_project_id=str(bool(getattr(args, 'project_id', None))))

    if getattr(args, "conversation_id", None):
        conv_id = args.conversation_id
        print(f"🔗 复用已有会话: {conv_id}", file=sys.stderr)
    else:
        skill_enabled = 1 if getattr(args, "deep_agent", False) else 0
        if skill_enabled:
            print("🧠 deepAgent 模式已开启 (skillEnabled=1)", file=sys.stderr)
        # 埋点：创建分析会话开始
        _lx_step('analyze_core-create_conv.start', '创建分析会话开始', mode='common')
        try:
            conv_id = create_conversation(args.prompt[:50], mode="common",
                                          project_id=args.project_id,
                                          skill_enabled=skill_enabled)
            print(f"📝 已创建会话: {conv_id}", file=sys.stderr)
            # 埋点：创建分析会话成功
            _lx_step('analyze_core-create_conv.success', '创建分析会话成功', conversation_id=str(conv_id))
        except SystemExit:
            # 埋点：创建分析会话失败
            _lx_step('analyze_core-create_conv.failure', '创建分析会话失败', error_message='create_conversation 失败')
            raise

    payload = {
        "conversationId": str(conv_id),
        "promptMessage": args.prompt,
        "rePlan": False,
        "source": 4,
    }
    if file_list:
        payload["fileList"] = file_list
    if fetch_data_request:
        payload["fetchDataRequest"] = fetch_data_request
        payload["extensions"] = []

    # 埋点：流式分析请求开始
    _lx_step('analyze_core-stream.start', '流式分析请求开始', conversation_id=str(conv_id))
    content_list, state, ended, _plan_block_id, last_chat_response_id = stream_and_collect(
        "/baagent/api/v2/chat/streamV2", payload)

    if not content_list and not ended:
        # 埋点：流式分析请求失败
        _lx_step('analyze_core-stream.failure', '流式分析请求失败',
                 error_message='分析流异常终止，未收到有效内容',
                 conversation_id=str(conv_id))
        print("❌ 分析流异常终止，未收到有效内容，请检查网络或重试", file=sys.stderr)
        sys.exit(1)

    # 埋点：流式分析请求成功
    _lx_step('analyze_core-stream.success', '流式分析请求成功', conversation_id=str(conv_id))

    summary_segs = extract_summary_segments(content_list)
    if summary_segs is not None:
        result_md = format_content_to_markdown(summary_segs, token)
    else:
        result_md = format_content_to_markdown(content_list, token)
    chat_url = f"{BA_BASE_URL}/chat/{conv_id}"
    output = {
        "conversationId": str(conv_id),
        "chatUrl": chat_url,
        "markdown": result_md,
        "ended": bool(ended),
    }
    if last_chat_response_id:
        output["chatResponseId"] = last_chat_response_id
    if _chromium_hints:
        output["chromium_hint"] = "\n\n".join(_chromium_hints)
    print(json.dumps(output, ensure_ascii=False))


def cmd_plan(args):
    token = check_auth_or_exit()
    file_list, fetch_data_request = _build_file_list(args, token)

    # 埋点：规划任务路径入口
    _lx_step('plan_core-start', '规划任务开始',
             has_file=str(bool(getattr(args, 'file', None))))

    if getattr(args, "conversation_id", None):
        conv_id = args.conversation_id
        print(f"🔗 复用已有会话: {conv_id}", file=sys.stderr)
    else:
        skill_enabled = 1 if getattr(args, "deep_agent", False) else 0
        if skill_enabled:
            print("🧠 deepAgent 模式已开启 (skillEnabled=1)", file=sys.stderr)
        # 埋点：创建规划会话开始
        _lx_step('plan_core-create_conv.start', '创建规划会话开始', mode='intelCommon')
        conv_id = create_conversation(args.prompt[:50], mode="intelCommon",
                                      project_id=args.project_id,
                                      skill_enabled=skill_enabled)
        # 埋点：创建规划会话成功
        _lx_step('plan_core-create_conv.success', '创建规划会话成功', conversation_id=str(conv_id))
    print(f"📝 已创建规划会话: {conv_id}", file=sys.stderr)

    replan = getattr(args, "replan", False)
    payload = {
        "conversationId": str(conv_id),
        "promptMessage": args.prompt,
        "rePlan": replan,
        "source": 4,
    }
    if file_list:
        payload["fileList"] = file_list
    if fetch_data_request:
        payload["fetchDataRequest"] = fetch_data_request
        payload["extensions"] = []

    # 埋点：规划流式请求开始
    _lx_step('plan_core-stream.start', '规划流式请求开始', conversation_id=str(conv_id))
    content_list, state, ended, plan_block_id, _last_crid = stream_and_collect(
        "/baagent/api/v2/chat/streamV2", payload)

    plan_content = None
    chat_response_id = None
    for seg in (content_list or []):
        if seg.get("type") == "plan":
            plan_content = seg.get("content")
        if seg.get("type") == "chatResponseId" or seg.get("chatResponseId"):
            chat_response_id = seg.get("content") or seg.get("chatResponseId")
    # 优先使用流式消息中包含 plan 的块顶层 id（即 chat_response_id 的正确来源）
    if plan_block_id:
        chat_response_id = plan_block_id
    # 兜底：从 state 里尝试取 chatResponseId
    elif not chat_response_id and isinstance(state, dict):
        chat_response_id = state.get("chatResponseId")

    # 埋点：规划流式请求成功（生成计划）
    _lx_step('plan_core-stream.success', '规划流式请求成功', conversation_id=str(conv_id),
             has_plan=str(bool(plan_content)))

    chat_url = f"{BA_BASE_URL}/chat/{conv_id}"
    if plan_content:
        # 将 plan 列表拼成 Markdown，供子 agent 直接回传展示，无需主 agent 再解析 JSON
        plan_md_lines = ["📋 **BA-Agent 规划方案如下：**\n"]
        for i, step in enumerate(plan_content, 1):
            title = step.get("title", f"步骤 {i}")
            plan_md_lines.append(f"**步骤 {i}：{title}**")
            for line in (step.get("content") or []):
                plan_md_lines.append(f"- {line}")
            plan_md_lines.append("")
        plan_md_lines.append("请问是**确认执行**此计划，还是需要**调整规划**？")
        plan_md = "\n".join(plan_md_lines)

        out = {
            "conversationId": str(conv_id),
            "chatUrl": chat_url,
            "status": "needs_confirm",
            "plan": plan_content,
            "markdown": plan_md,
            "message": "规划模式需要用户确认计划后才能执行，请调用 confirm_plan 子命令继续"
        }
        if chat_response_id:
            out["chatResponseId"] = chat_response_id
        print(json.dumps(out, ensure_ascii=False))
        return

    result_md = extract_plan_result(content_list, state, token)
    output = {
        "conversationId": str(conv_id),
        "chatUrl": chat_url,
        "markdown": result_md,
    }
    if _chromium_hints:
        output["chromium_hint"] = "\n\n".join(_chromium_hints)
    print(json.dumps(output, ensure_ascii=False))


def cmd_confirm_plan(args):
    token = check_auth_or_exit()
    payload = {
        "conversationId": str(args.conversation_id),
        "chatResponseId": args.chat_response_id,
        "plan": json.loads(args.plan_json) if args.plan_json else [],
    }
    # 埋点：确认执行计划开始
    _lx_step('plan_core-confirm.start', '确认执行计划开始', conversation_id=str(args.conversation_id))
    print("🚀 开始执行规划任务...", file=sys.stderr, flush=True)
    content_list, last_state, ended, _plan_block_id, _last_crid = stream_and_collect(
        "/baagent/api/v2/chat/startTask", payload)
    if ended:
        # 埋点：确认执行计划成功
        _lx_step('plan_core-confirm.success', '确认执行计划成功', conversation_id=str(args.conversation_id))
    else:
        # 埋点：确认执行计划失败
        _lx_step('plan_core-confirm.failure', '确认执行计划失败',
                 error_message='执行规划任务未正常结束',
                 conversation_id=str(args.conversation_id))
    result_md = extract_plan_result(content_list, last_state, token)
    chat_url = f"{BA_BASE_URL}/chat/{args.conversation_id}"
    output = {
        "conversationId": str(args.conversation_id),
        "chatUrl": chat_url,
        "markdown": result_md,
    }
    if _chromium_hints:
        output["chromium_hint"] = "\n\n".join(_chromium_hints)
    print(json.dumps(output, ensure_ascii=False))


def cmd_reconnect(args):
    """流式断开后重连会话，继续等待 BA-Agent 分析结果。

    当 analyze 返回 ended=false（流式中途断开、内置自动重连未完成）时，用返回的
    conversationId + chatResponseId 调本命令续传。内部走 reconnectV2 重连循环，
    若仍拿不到完整结论则从 historyListV2 兜底补全最新一轮 server 消息。
    返回结构与 analyze 一致（含 ended/chatResponseId），由 Agent 自行决定是否调用。
    """
    token = check_auth_or_exit()
    conv_id = str(args.conversation_id)
    crid = args.chat_response_id
    max_rc = getattr(args, "max_reconnect", 5)

    _lx_step('reconnect_core-start', '断线重连开始', conversation_id=conv_id)
    print(f"🔁 开始重连会话 {conv_id}（chatResponseId={crid or '自动从历史消息获取'}）...",
          file=sys.stderr, flush=True)

    last_content, _last_state, ended, _plan_block_id, last_crid = reconnect_stream(
        conv_id, crid, max_reconnect=max_rc)

    summary_segs = extract_summary_segments(last_content)

    # history 兜底：完全没内容，或未结束且无 summary → 从 historyListV2 拉最新一轮 server 消息补全
    if not last_content or (not ended and summary_segs is None):
        print("📊 重连未拿到完整结论，从历史消息补全...", file=sys.stderr, flush=True)
        referer = f"{BA_BASE_URL}/chat/{conv_id}"
        history = http_get("/baagent/api/v2/chat/historyListV2",
                           params={"conversationId": conv_id, "pageNo": 1, "pageSize": 50},
                           token=token, referer=referer)
        if history.get("code") == 0:
            messages = (history.get("data") or {}).get("data", []) or []
            server_msgs = [m for m in messages if m.get("role") == "server"]
            if server_msgs:
                last_content = server_msgs[-1].get("content", []) or last_content
                summary_segs = extract_summary_segments(last_content)
                ended = True  # 历史已落盘视为完成

    if not last_content:
        _lx_step('reconnect_core-stream.failure', '重连失败',
                 error_message='重连未收到有效内容', conversation_id=conv_id)
        print(f"❌ 重连未收到有效内容，且历史消息无可用结果，"
              f"请前往 {BA_BASE_URL}/chat/{conv_id} 查看", file=sys.stderr)
        sys.exit(1)

    _lx_step('reconnect_core-stream.success', '重连成功', conversation_id=conv_id)
    result_md = (format_content_to_markdown(summary_segs, token)
                 if summary_segs is not None
                 else format_content_to_markdown(last_content, token))

    output = {
        "conversationId": conv_id,
        "chatUrl": f"{BA_BASE_URL}/chat/{conv_id}",
        "markdown": result_md,
        "ended": bool(ended),
    }
    if last_crid:
        output["chatResponseId"] = last_crid
    if _chromium_hints:
        output["chromium_hint"] = "\n\n".join(_chromium_hints)
    print(json.dumps(output, ensure_ascii=False))


def cmd_share(args):
    result = http_post("/baagent/api/v2/dialogue/share",
                       data={"id": str(args.conversation_id)})
    if result.get("code") != 0:
        print(f"❌ 分享失败: {result.get('msg')}")
        sys.exit(1)
    share_id = result["data"]
    share_url = f"{BA_BASE_URL}/share/{share_id}"
    print(json.dumps({"shareUrl": share_url}, ensure_ascii=False))


def _enrich_chart_html(seg_data, token):
    """
    对 chart/table 类型的 data 对象，若 htmlContent 为空但有 chartTableId，
    则调用 /baagent/api/v2/report/content 补齐 htmlContent（最多重试 2 次）。
    直接修改并返回传入的 dict，失败时打印 warning 但不中断。
    """
    if not isinstance(seg_data, dict):
        return seg_data
    if seg_data.get("htmlContent"):
        return seg_data
    chart_id = seg_data.get("chartTableId") or seg_data.get("id")
    chart_name = seg_data.get("name", "图表")
    if not chart_id or not token:
        return seg_data
    for attempt in range(1, 3):
        try:
            print(f"  ⏳ 补齐图表 [{chart_name}] htmlContent，第 {attempt} 次请求...", file=sys.stderr, flush=True)
            result = http_get("/baagent/api/v2/report/content",
                              params={"id": chart_id}, token=token, timeout=120)
            data = result.get("data", {}) or {}
            status = data.get("status")
            if status == "SUCCESS":
                seg_data["htmlContent"] = data.get("content", "")
                print(f"  ✅ 图表 [{chart_name}] htmlContent 补齐成功", file=sys.stderr)
                return seg_data
            else:
                error_type = data.get("errorType") or {}
                error_msg = error_type.get("message") if isinstance(error_type, dict) else str(error_type)
                print(f"  ⚠️ 图表 [{chart_name}] 第 {attempt} 次返回状态: {status}，错误: {error_msg}", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️ 图表 [{chart_name}] 第 {attempt} 次请求异常: {e}", file=sys.stderr)
    print(f"  ⚠️ 图表 [{chart_name}] htmlContent 补齐失败，使用原始数据继续", file=sys.stderr)
    return seg_data


def _build_share_content_from_segments(segments, token):
    """
    将 segment 列表转换为 resultShare 接口所需的 content 列表。
    chart/table 类型会尝试补齐 htmlContent。
    """
    content_to_share = []
    for seg in segments:
        seg_type = seg.get("type")
        seg_data = seg.get("content")
        if seg_type in ("markdown", "text"):
            content_to_share.append({"contentType": seg_type, "data": seg_data})
        elif seg_type in ("chart", "table"):
            enriched = _enrich_chart_html(seg_data, token)
            content_to_share.append({"contentType": seg_type, "data": enriched})
    return content_to_share


def _resolve_round(round_arg, total_rounds):
    """
    将用户传入的轮数描述解析为 1-based 正整数索引，或 None（表示全部）。
    支持：
      - None / 空字符串         → None（全部）
      - 正整数字符串 "2"        → 2（第 2 轮）
      - 负整数字符串 "-1"       → total_rounds（最后一轮）
      - "-2"                    → total_rounds - 1（倒第 2 轮）
      - 关键词 "last" / "最后"  → total_rounds
      - 关键词 "last-N" / "倒第N轮" 等 → total_rounds - N + 1
    返回 (resolved_idx_or_None, error_msg_or_None)
    """
    if round_arg is None:
        return None, None
    # 已经是 int（argparse 解析后）
    if isinstance(round_arg, int):
        idx = round_arg if round_arg > 0 else total_rounds + round_arg + 1
        return idx, None
    s = str(round_arg).strip()
    if not s:
        return None, None
    # 纯数字（含负数）
    try:
        n = int(s)
        idx = n if n > 0 else total_rounds + n + 1
        return idx, None
    except ValueError:
        pass
    # 关键词匹配
    import re as _re
    s_lower = s.lower()
    # "last" / "最后" / "最后一轮" → 最后一轮
    if s_lower in ("last", "最后", "最后一轮", "最后一次"):
        return total_rounds, None
    # "last-N" / "倒第N轮" / "倒数第N轮" / "倒第N" / "倒数第N"
    m = _re.match(r"last[-_]?(\d+)$", s_lower)
    if m:
        n = int(m.group(1))
        return total_rounds - n + 1, None
    m = _re.search(r"倒[数第]*(\d+)[轮次]?", s)
    if m:
        n = int(m.group(1))
        return total_rounds - n + 1, None
    return None, f"无法识别的轮数描述：{round_arg!r}，请传入数字（如 1、-1）或关键词（如 last、倒第2轮）"


def cmd_share_to_km(args):
    token = check_auth_or_exit()
    round_arg = getattr(args, "round", None)  # 用户指定的轮数描述，None 表示不指定

    # ── 第一步：获取会话详情，判断模式 ──────────────────────────────
    referer = f"{BA_BASE_URL}/chat/{args.conversation_id}"
    detail_resp = http_get("/baagent/api/v2/dialogue/detail",
                           params={"id": str(args.conversation_id)},
                           token=token, referer=referer)
    if detail_resp.get("code") != 0:
        print(f"❌ 获取会话详情失败: {detail_resp.get('msg')}")
        sys.exit(1)
    detail_data = detail_resp.get("data", {}) or {}
    mode = (detail_data.get("ctx") or {}).get("mode", "")

    content_to_share = []

    if mode == "intelCommon":
        # ── 规划模式：从 workspace 的 result 中提取 version.contents ──
        # workspace 数组中第 N 位对应第 N 轮（1-based）
        print("📋 规划模式，从 workspace 提取最终结果...", file=sys.stderr)
        workspace_list = detail_data.get("workspace") or []

        # 收集所有可用的 result 条目（按 workspace 数组顺序）
        available_rounds = []
        for ws in workspace_list:
            for f in ws.get("file", []):
                if f.get("type") == "result":
                    fc = f.get("content", {}) or {}
                    version = fc.get("version", {}) or {}
                    contents = version.get("contents", [])
                    if contents:
                        available_rounds.append(contents)

        total_rounds = len(available_rounds)
        if total_rounds == 0:
            print("❌ 规划模式下未找到可分享的最终结果（team_answer），请确认任务已执行完毕")
            sys.exit(1)

        # 解析用户指定的轮数
        round_idx, resolve_err = _resolve_round(round_arg, total_rounds)
        if resolve_err:
            print(json.dumps({"error": resolve_err}, ensure_ascii=False))
            sys.exit(1)

        if round_idx is not None:
            if round_idx < 1 or round_idx > total_rounds:
                print(json.dumps({
                    "error": f"指定的轮数 {round_idx} 超出范围，当前会话共有 {total_rounds} 轮结果，请指定 1~{total_rounds} 之间的轮数"
                }, ensure_ascii=False))
                sys.exit(1)
            content_to_share = available_rounds[round_idx - 1]
            print(f"📌 使用第 {round_idx} 轮结果（共 {total_rounds} 轮）", file=sys.stderr)
        else:
            # 不指定轮数：拼接所有轮次的 contents
            for r in available_rounds:
                content_to_share.extend(r)
            print(f"📌 合并全部 {total_rounds} 轮结果", file=sys.stderr)

    else:
        # ── 分析模式：从 historyListV2 提取 summary 区间 ────────────
        # server 消息数组中第 N 个（过滤 role=server 后）对应第 N 轮（1-based）
        print("📊 分析模式，从历史消息提取 summary 结论区间...", file=sys.stderr)
        referer = f"{BA_BASE_URL}/chat/{args.conversation_id}"
        history = http_get("/baagent/api/v2/chat/historyListV2",
                           params={"conversationId": str(args.conversation_id),
                                   "pageNo": 1, "pageSize": 50},
                           token=token, referer=referer)
        if history.get("code") != 0:
            print(f"❌ 获取历史消息失败: {history.get('msg')}")
            sys.exit(1)

        messages = history.get("data", {}).get("data", [])
        # 按 role=server 过滤，每个 server 消息对应一轮
        server_messages = [msg for msg in messages if msg.get("role") == "server"]
        total_rounds = len(server_messages)

        if total_rounds == 0:
            print("❌ 分析模式下未找到任何 server 消息，请确认分析已完成")
            sys.exit(1)

        # 解析用户指定的轮数
        round_idx, resolve_err = _resolve_round(round_arg, total_rounds)
        if resolve_err:
            print(json.dumps({"error": resolve_err}, ensure_ascii=False))
            sys.exit(1)

        if round_idx is not None:
            if round_idx < 1 or round_idx > total_rounds:
                print(json.dumps({
                    "error": f"指定的轮数 {round_idx} 超出范围，当前会话共有 {total_rounds} 轮结果，请指定 1~{total_rounds} 之间的轮数"
                }, ensure_ascii=False))
                sys.exit(1)
            # 只取指定轮次的 server 消息 segments
            segments = server_messages[round_idx - 1].get("content", [])
            print(f"📌 使用第 {round_idx} 轮结果（共 {total_rounds} 轮）", file=sys.stderr)
        else:
            # 不指定轮数：拼接所有 server 消息的 segments
            segments = []
            for msg in server_messages:
                segments.extend(msg.get("content", []))
            print(f"📌 合并全部 {total_rounds} 轮结果", file=sys.stderr)

        # 提取 summary 区间
        summary_segs = extract_summary_segments(segments)
        if not summary_segs:
            print("❌ 分析模式下未找到 summary 结论区间，无法分享（请确认分析已完成）")
            sys.exit(1)

        content_to_share = _build_share_content_from_segments(summary_segs, token)
        if not content_to_share:
            print("❌ summary 区间内未提取到有效内容，无法分享")
            sys.exit(1)

    # ── 调用 resultShare 写入学城 ─────────────────────────────────
    payload = {
        "id": str(args.conversation_id),
        "type": "km",
        "kmUrl": args.km_url,
        "title": args.title or "BA-Agent 分析结果",
        "content": content_to_share,
    }
    result = http_post("/baagent/api/v2/chat/resultShare", data=payload)
    if result.get("code") != 0:
        print(f"❌ 分享至学城失败: {result.get('msg')}")
        sys.exit(1)
    print(json.dumps({"kmUrl": result.get("data")}, ensure_ascii=False))


def cmd_projects(args):
    # 埋点：项目查询路径入口
    _lx_step('projects-start', '项目查询开始', action='list')
    # 默认使用 authList（有权限使用的项目），传 --personal 时使用个人项目列表
    use_personal = getattr(args, "personal", False)
    if use_personal:
        endpoint = "/baagent/api/v2/project/list"
    else:
        endpoint = "/baagent/api/v2/project/authList"
    page_no = getattr(args, "page_no", 1)
    page_size = getattr(args, "page_size", 20)
    result = http_get(endpoint,
                      params={"pageNo": page_no, "pageSize": page_size})
    if result.get("code") != 0:
        # 埋点：项目列表获取失败
        _lx_step('projects-list.failure', '项目列表获取失败', error_message=str(result.get('msg', '')))
        print(f"❌ 项目列表失败: {result.get('msg')}")
        sys.exit(1)
    data = result.get("data", {})
    items = data.get("data", data.get("list", []))
    total = data.get("total", None)
    actual_page_no = data.get("pageNo", page_no)
    count = len(items)
    # 埋点：项目列表获取成功
    _lx_step('projects-list.success', '项目列表获取成功', count=str(count))
    print(json.dumps({
        "projects": items,
        "pageNo": actual_page_no,
        "pageSize": page_size,
        "count": count,
        "total": total
    }, ensure_ascii=False))


def get_project_info(project_id, token=None):
    """
    根据 project_id 从项目列表中获取项目信息，返回：
    {
        "files": [...],        # 普通文件列表（可直接放入 fileList）
        "sourceList": [...],   # inputInfo.sourceList（沧澜数据集，通过 --canglan-source-json 传入 analyze/plan）
        "ctx": {...},          # 项目 ctx（用于 create 会话时透传）
    }
    找不到项目时返回 None。
    authList 已包含个人项目，只查 authList 即可。
    """
    result = http_get("/baagent/api/v2/project/authList",
                      params={"pageNo": 1, "pageSize": 100, "projectId": project_id}, token=token)
    if not isinstance(result, dict):
        print(f"❌ 获取项目列表异常：接口返回非 JSON 内容（可能是 token 过期或网络问题），请重新鉴权后重试", file=sys.stderr)
        sys.exit(2)
    if result.get("code") != 0:
        print(f"❌ 获取项目列表失败（code={result.get('code')}）: {result.get('msg')}，请检查 token 是否有效", file=sys.stderr)
        sys.exit(2)
    items = result.get("data", {}).get("data", result.get("data", {}).get("list", []))
    if not items:
        print(f"⚠️ 项目列表为空（接口返回 0 个项目），可能是 token 已过期或账号下暂无项目", file=sys.stderr)
        return None
    proj = next((p for p in items if str(p.get("id")) == str(project_id)), None)
    if proj is not None:
        # 普通文件
        files = proj.get("files") or []
        file_list = []
        for f in files:
            if not f.get("s3Name"):
                continue
            file_list.append({
                "originFileName": f.get("originFileName"),
                "s3Name": f.get("s3Name"),
                "dataType": f.get("dataType", "file"),
                "size": f.get("size", ""),
                "hasAuth": True,
            })
        # 沧澜数据集列表
        input_info = proj.get("inputInfo") or {}
        source_list = input_info.get("sourceList") or []
        # 项目 ctx
        ctx = proj.get("ctx") or {}
        if file_list:
            print(f"📎 项目 {project_id} 包含 {len(file_list)} 个普通文件: "
                  f"{[x['originFileName'] for x in file_list]}", file=sys.stderr)
        if source_list:
            print(f"📊 项目 {project_id} 包含 {len(source_list)} 个沧澜数据集，需通过 --canglan-source-json 传入",
                  file=sys.stderr)
        return {"files": file_list, "sourceList": source_list, "ctx": ctx}
    print(f"⚠️ 未在项目列表中找到 project_id={project_id}", file=sys.stderr)
    return None


def _get_project_files(project_id, token=None):
    """
    兼容旧逻辑：只返回普通文件列表（无沧澜数据集）。
    新的项目+沧澜数据集流程请使用 project_info 获取 sourceList，再通过 --canglan-source-json 传入。
    """
    info = get_project_info(project_id, token=token)
    if info is None:
        return []
    return info["files"]


def cmd_project_info(args):
    """
    获取单个项目的详细信息，包含：普通文件列表、沧澜数据集 sourceList、ctx。
    供主 agent 在项目模式下调用，sourceList 通过 --canglan-source-json 传入 analyze/plan。
    """
    token = check_auth_or_exit()
    info = get_project_info(args.project_id, token=token)
    if info is None:
        print(f"❌ 未找到 project_id={args.project_id}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(info, ensure_ascii=False))


def cmd_fetch_data(args):
    """
    [已废弃] 新流程下无需单独取数，沧澜数据集信息通过 --canglan-source-json 直接传入
    analyze/plan 命令，由 streamV2 后端自动取数。保留此命令仅用于向后兼容。

    触发沧澜数据集取数（fetchData 接口）。
    输入：--source-list-json <sourceList JSON 字符串>（来自 project_info 的 sourceList）
    输出：
    {
        "success": [...],   # status=1 的取数结果（可直接转换为 fileList 条目）
        "failed": [...],    # status!=1 的失败记录（含 message 错误原因）
    }
    """
    print("⚠️ [废弃提示] fetch_data 命令已废弃。新流程请使用 --canglan-source-json 参数直接传入 analyze/plan 命令。", file=sys.stderr)
    import random
    import string

    source_list = json.loads(args.source_list_json)
    if not source_list:
        print(json.dumps({"success": [], "failed": []}, ensure_ascii=False))
        return

    datas = []
    for item in source_list:
        front_ext = item.get("frontExtInfo") or {}
        subscription_list = front_ext.get("subscriptionInfoList") or []
        subscription_info = subscription_list[0] if subscription_list else {}

        # uniqueId：优先使用 frontExtInfo.uniqueId，否则自动生成
        unique_id = front_ext.get("uniqueId")
        if not unique_id:
            rand_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
            unique_id = f"{item.get('id', '')}_{int(time.time() * 1000)}_{rand_str}"

        entry = {
            "uniqueId": unique_id,
            "id": item.get("id"),
            "type": item.get("type"),
            "datasetName": item.get("name"),
            "businessLineId": front_ext.get("businessLineId"),
        }

        metric_list = front_ext.get("metricList")
        if metric_list is not None:
            entry["metricList"] = metric_list

        dim_list = subscription_info.get("dimList")
        if dim_list is not None:
            entry["dimList"] = dim_list

        date_range = front_ext.get("dateRangeOriginalValue")
        if date_range is not None:
            entry["dateRange"] = date_range

        if subscription_info:
            sub = {}
            for k in ("name", "id", "version"):
                if subscription_info.get(k) is not None:
                    sub[k] = subscription_info[k]
            if sub:
                entry["subscription"] = sub

        datas.append(entry)

    result = http_post("/baagent/api/v2/extension/fetchData", data={"datas": datas})
    if result.get("code") != 0:
        print(f"❌ fetchData 接口失败: {result.get('msg')}", file=sys.stderr)
        sys.exit(1)

    resp_list = result.get("data") or []
    success_items = []
    failed_items = []
    for r in resp_list:
        if r.get("status") == 1:
            success_items.append(r)
        else:
            failed_items.append(r)

    if success_items:
        print(f"✅ 取数成功 {len(success_items)} 个数据集", file=sys.stderr)
    if failed_items:
        print(f"⚠️ 取数失败 {len(failed_items)} 个数据集", file=sys.stderr)

    print(json.dumps({"success": success_items, "failed": failed_items}, ensure_ascii=False))

def cmd_history(args):
    token = check_auth_or_exit()
    referer = f"{BA_BASE_URL}/chat/{args.conversation_id}"
    result = http_get("/baagent/api/v2/chat/historyListV2",
                      params={"conversationId": str(args.conversation_id),
                              "pageNo": 1, "pageSize": 50},
                      token=token, referer=referer)
    if result.get("code") != 0:
        print(f"❌ 获取历史失败: {result.get('msg')}")
        sys.exit(1)
    print(json.dumps(result.get("data", {}), ensure_ascii=False))



def cmd_create_conv(args):
    """
    仅创建会话，返回 conversationId，不执行任何分析。
    --mode analyze      → common（默认）
    --mode plan         → intelCommon（规划模式）
    --ctx-json          → 直接传入项目 ctx JSON 字符串（来自 project_info.ctx），优先级高于 --mode
    --deep-agent        → 开启 deepAgent 模式（skillEnabled=1），默认关闭
    主 agent 应先调用此命令拿到 conversationId，再 spawn 与其绑定的子 agent。
    """
    mode = "intelCommon" if getattr(args, "mode", "analyze") == "plan" else "common"
    name = getattr(args, "name", None) or "新会话"
    ctx_override = None
    if getattr(args, "ctx_json", None):
        try:
            ctx_override = json.loads(args.ctx_json)
        except Exception as e:
            print(f"❌ --ctx-json 解析失败: {e}", file=sys.stderr)
            sys.exit(1)
    skill_enabled = 1 if getattr(args, "deep_agent", False) else 0
    if skill_enabled:
        print("🧠 deepAgent 模式已开启 (skillEnabled=1)", file=sys.stderr)
    conv_id = create_conversation(name[:50], mode=mode,
                                  project_id=getattr(args, "project_id", None),
                                  ctx_override=ctx_override,
                                  skill_enabled=skill_enabled)
    print(json.dumps({"conversationId": str(conv_id)}, ensure_ascii=False))


def cmd_conversations(args):
    """获取会话列表，支持按关键词搜索"""
    params = {
        "pageNo": getattr(args, "page", 1),
        "pageSize": getattr(args, "page_size", 20),
    }
    keyword = getattr(args, "keyword", None)
    if keyword:
        params["keyword"] = keyword
    result = http_get("/baagent/api/v2/dialogue/list", params=params)
    if result.get("code") != 0:
        print(f"❌ 获取会话列表失败: {result.get('msg')}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result.get("data", {}), ensure_ascii=False))


def cmd_conversation_detail(args):
    """获取会话详情（含工作空间文件、配置）"""
    referer = f"{BA_BASE_URL}/chat/{args.conversation_id}"
    result = http_get("/baagent/api/v2/dialogue/detail",
                      params={"id": str(args.conversation_id)}, referer=referer)
    if result.get("code") != 0:
        print(f"❌ 获取会话详情失败: {result.get('msg')}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result.get("data", {}), ensure_ascii=False))


def cmd_interrupt(args):
    """中断正在执行的分析任务"""
    payload = {
        "conversationId": str(args.conversation_id),
        "chatResponseId": args.chat_response_id,
    }
    result = http_post("/baagent/api/v2/chat/interrupt", data=payload)
    if result.get("code") != 0:
        print(f"❌ 中断失败: {result.get('msg')}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"success": True}, ensure_ascii=False))


def cmd_evaluate(args):
    """对回复点赞(1)或点踩(2)"""
    payload = {
        "id": str(args.message_id),
        "feedBack": int(args.score),
    }
    result = http_post("/baagent/api/v2/chat/evaluate", data=payload)
    if result.get("code") != 0:
        print(f"❌ 反馈失败: {result.get('msg')}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"success": True}, ensure_ascii=False))


def cmd_user_info(args):
    """
    获取当前登录用户信息。
    退出码：
      0  已登录且（未传 --expected-mis 或 misId 匹配）
      2  未登录 / token 无效
      3  misId 不匹配（已自动清除 token）
    """
    token = get_access_token()
    if not token:
        print("❌ 未检测到 access-token，请先完成 SSO 登录", file=sys.stderr)
        sys.exit(2)

    requests = _ensure_requests()
    headers = {
        "access-token": token,
        "Content-Type": "application/json",
        "X-Source": "catclaw",
        "Cookie": _build_cookie_header(token),
    }
    try:
        resp = requests.get(
            BA_BASE_URL + "/baagent/api/v2/platform/getUserInfo",
            headers=headers,
            timeout=15,
            allow_redirects=False,
        )
    except Exception as e:
        print(f"❌ 请求 getUserInfo 失败: {e}", file=sys.stderr)
        sys.exit(1)

    # token 过期或被重定向到 SSO
    if _is_expired_response(resp):
        print("❌ 当前 token 已过期或无效，请重新 SSO 登录", file=sys.stderr)
        sys.exit(2)

    try:
        data = resp.json()
    except Exception:
        print(f"❌ getUserInfo 响应解析失败（status={resp.status_code}）", file=sys.stderr)
        sys.exit(1)

    # 接口业务层判断未登录
    if data.get("code") != 0:
        print(f"❌ 未登录或 token 无效（code={data.get('code')}, msg={data.get('msg')}），请重新 SSO 登录", file=sys.stderr)
        sys.exit(2)

    user = data.get("data") or {}
    mis_id = (
        user.get("misId")
        or user.get("mis_id")
        or user.get("loginName")
        or user.get("userName")
        or ""
    )

    # misId 不匹配检查
    expected = getattr(args, "expected_mis", None)
    if expected and mis_id and mis_id.lower() != expected.lower():
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        print(
            f"❌ 当前登录用户 ({mis_id}) 与期望用户 ({expected}) 不匹配，已清除 token，请重新 SSO 登录",
            file=sys.stderr,
        )
        sys.exit(3)

    print(json.dumps({"misId": mis_id, "userInfo": user}, ensure_ascii=False))


# ─────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BA-Agent API 调用脚本")
    parser.add_argument(
        "--env",
        choices=["prod", "st", "staging"],
        default=None,
        help="指定环境：prod(线上,默认) / st|staging(预上线)。测试环境因环境隔离不支持。",
    )
    parser.add_argument(
        "--gray-release",
        dest="gray_release",
        default=None,
        help="灰度链路标识（如 gray-release-baagent-prod-test），设置后所有请求 Header 中自动添加 gray-release-set。",
    )
    sub = parser.add_subparsers(dest="cmd")

    # analyze
    p_analyze = sub.add_parser("analyze", help="通用分析模式")
    p_analyze.add_argument("prompt", help="分析提示词")
    p_analyze.add_argument("--file", help="本地文件路径")
    p_analyze.add_argument("--display-name", dest="display_name", help="文件上传时的显示名称")
    p_analyze.add_argument("--km-url", dest="km_url", help="学城文档 URL")
    p_analyze.add_argument("--project-id", dest="project_id", help="项目 ID")
    p_analyze.add_argument("--conversation-id", dest="conversation_id", help="复用已有会话 ID")
    p_analyze.add_argument("--canglan-source-json", dest="canglan_source_json", default=None,
                           help="沧澜数据集源信息 JSON（来自 project_info 的 sourceList 或直接指定的数据集元信息）")
    p_analyze.add_argument("--no-reauth", dest="no_reauth", action="store_true", default=False,
                           help="禁用自动重鉴权（子 agent 场景使用，遇到 token 过期直接报错返回）")
    p_analyze.add_argument("--deep-agent", dest="deep_agent", action="store_true", default=False,
                           help="开启 deepAgent 模式（skillEnabled=1），仅在新建会话时生效")

    # plan
    p_plan = sub.add_parser("plan", help="规划模式")
    p_plan.add_argument("prompt", help="分析提示词")
    p_plan.add_argument("--file", help="本地文件路径")
    p_plan.add_argument("--display-name", dest="display_name", help="文件上传时的显示名称")
    p_plan.add_argument("--km-url", dest="km_url", help="学城文档 URL")
    p_plan.add_argument("--project-id", dest="project_id", help="项目 ID")
    p_plan.add_argument("--conversation-id", dest="conversation_id", help="复用已有会话 ID")
    p_plan.add_argument("--replan", action="store_true", default=False, help="重新规划（rePlan=true），需配合 --conversation-id 使用")
    p_plan.add_argument("--canglan-source-json", dest="canglan_source_json", default=None,
                        help="沧澜数据集源信息 JSON（来自 project_info 的 sourceList 或直接指定的数据集元信息）")
    p_plan.add_argument("--no-reauth", dest="no_reauth", action="store_true", default=False,
                        help="禁用自动重鉴权（子 agent 场景使用，遇到 token 过期直接报错返回）")
    p_plan.add_argument("--deep-agent", dest="deep_agent", action="store_true", default=False,
                        help="开启 deepAgent 模式（skillEnabled=1），仅在新建会话时生效")

    # confirm_plan
    p_confirm = sub.add_parser("confirm_plan", help="确认并执行规划")
    p_confirm.add_argument("conversation_id", help="会话 ID")
    p_confirm.add_argument("chat_response_id", nargs="?", default="", help="chatResponseId（可选，plan 阶段未返回时可省略）")
    p_confirm.add_argument("--plan-json", dest="plan_json", help="plan JSON 字符串")

    # reconnect
    p_reconnect = sub.add_parser(
        "reconnect",
        help="流式断开后重连会话继续等待分析结果（analyze 返回 ended=false 时使用）")
    p_reconnect.add_argument("conversation_id", help="会话 ID")
    p_reconnect.add_argument("chat_response_id", nargs="?", default=None,
                             help="analyze 返回的 chatResponseId（可选：未提供时自动从历史消息取最新 id）")
    p_reconnect.add_argument("--max-reconnect", dest="max_reconnect", type=int, default=5,
                             help="最大重连次数（默认 5）")
    p_reconnect.add_argument("--no-reauth", dest="no_reauth", action="store_true", default=False,
                             help="禁用自动重鉴权（子 agent 场景使用）")

    # share
    p_share = sub.add_parser("share", help="生成分享链接")
    p_share.add_argument("conversation_id", help="会话 ID")

    # share_to_km
    p_km = sub.add_parser("share_to_km", help="发布到学城")
    p_km.add_argument("conversation_id", help="会话 ID")
    p_km.add_argument("--title", help="学城文档标题")
    p_km.add_argument("--km-url", dest="km_url", help="目标学城文档 URL（如 https://km.sankuai.com/collabpage/xxx）")
    p_km.add_argument("--round", dest="round", type=str, default=None,
                      help="指定写入第几轮的结果。不指定则写入全部轮次内容。"
                           "支持：正整数（第N轮）、负整数（-1=最后一轮）、"
                           "关键词 last/最后/最后一轮、倒第N轮/倒数第N轮/last-N。"
                           "分析模式：按 role=server 过滤后的第 N 个消息；"
                           "规划模式：workspace 数组中第 N 位。")

    # projects
    p_projects = sub.add_parser("projects", help="列出项目")
    p_projects.add_argument("--personal", action="store_true",
                            help="仅列出个人项目（默认列出有权限使用的项目）")
    p_projects.add_argument("--page-no", dest="page_no", type=int, default=1,
                            help="当前页数（默认 1）")
    p_projects.add_argument("--page-size", dest="page_size", type=int, default=20,
                            help="每页条数（默认 20）")

    # project_info
    p_proj_info = sub.add_parser("project_info", help="获取单个项目详情（含普通文件、沧澜数据集 sourceList、ctx）")
    p_proj_info.add_argument("project_id", help="项目 ID")

    # fetch_data
    p_fetch_data = sub.add_parser("fetch_data", help="触发沧澜数据集取数（主 agent 调用，需与用户交互确认失败数据集）")
    p_fetch_data.add_argument("--source-list-json", dest="source_list_json", required=True,
                              help="sourceList JSON 字符串（来自 project_info.sourceList）")

    # history
    p_hist = sub.add_parser("history", help="获取对话历史")
    p_hist.add_argument("conversation_id", help="会话 ID")

    # user_info
    p_uinfo = sub.add_parser("user_info", help="获取当前登录用户信息（用于鉴权校验）")
    p_uinfo.add_argument("--expected-mis", dest="expected_mis", default=None,
                         help="期望的 misId，不匹配时自动清除 token")


    # create_conv
    p_create_conv = sub.add_parser("create_conv", help="仅创建会话，返回 conversationId（不执行分析）")
    p_create_conv.add_argument("--name", default="新会话", help="会话名称（默认：新会话）")
    p_create_conv.add_argument("--mode", choices=["analyze", "plan"], default="analyze",
                               help="analyze=通用分析，plan=规划模式（默认 analyze）")
    p_create_conv.add_argument("--project-id", dest="project_id", default=None, help="项目 ID")
    p_create_conv.add_argument("--ctx-json", dest="ctx_json", default=None,
                               help="项目 ctx JSON 字符串（来自 project_info.ctx），传入后直接透传给 create 接口，忽略 --mode")
    p_create_conv.add_argument("--deep-agent", dest="deep_agent", action="store_true", default=False,
                               help="开启 deepAgent 模式（skillEnabled=1），默认关闭")

    # conversations
    p_convs = sub.add_parser("conversations", help="获取会话列表")
    p_convs.add_argument("--keyword", default=None, help="关键词搜索")
    p_convs.add_argument("--page", type=int, default=1, help="页码（默认 1）")
    p_convs.add_argument("--page-size", dest="page_size", type=int, default=20, help="每页数量（默认 20）")

    # conversation_detail
    p_conv_detail = sub.add_parser("conversation_detail", help="获取会话详情")
    p_conv_detail.add_argument("conversation_id", help="会话 ID")

    # interrupt
    p_interrupt = sub.add_parser("interrupt", help="中断正在执行的分析任务")
    p_interrupt.add_argument("conversation_id", help="会话 ID")
    p_interrupt.add_argument("chat_response_id", help="chatResponseId")

    # evaluate
    p_evaluate = sub.add_parser("evaluate", help="对回复点赞(1)或点踩(2)")
    p_evaluate.add_argument("message_id", help="消息 ID")
    p_evaluate.add_argument("score", type=int, choices=[1, 2], help="1=点赞，2=点踩")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    # 根据 --env 参数动态覆盖 BA_BASE_URL / BA_DATA_DIR / TOKEN_FILE / BA_ENV
    global BA_BASE_URL, BA_DATA_DIR, TOKEN_FILE
    if hasattr(args, "env") and args.env:
        if args.env == "test":
            print("❌ 测试环境因环境隔离原因暂不支持，请使用 prod 或 st 环境。", file=sys.stderr)
            sys.exit(1)
        BA_BASE_URL = _ENV_URL_MAP.get(args.env, BA_BASE_URL)
        # 同步更新运行时状态目录（按环境隔离）和 BA_ENV 环境变量
        if not os.environ.get("BA_DATA_DIR"):
            BA_DATA_DIR = _default_data_dir(args.env)
            os.makedirs(BA_DATA_DIR, exist_ok=True)
            TOKEN_FILE = os.path.join(BA_DATA_DIR, "access_token.txt")
        os.environ["BA_ENV"] = args.env

    # 根据 --no-reauth 参数设置全局禁用重鉴权 flag
    global _NO_REAUTH
    if hasattr(args, "no_reauth") and args.no_reauth:
        _NO_REAUTH = True

    # 根据 --gray-release 参数或环境变量设置灰度链路
    global _GRAY_RELEASE_SET
    if hasattr(args, "gray_release") and args.gray_release:
        _GRAY_RELEASE_SET = args.gray_release.strip()
        print(f"🔀 灰度链路已启用: gray-release-set={_GRAY_RELEASE_SET}", file=sys.stderr)

    cmds = {
        "analyze": cmd_analyze,
        "plan": cmd_plan,
        "confirm_plan": cmd_confirm_plan,
        "reconnect": cmd_reconnect,
        "create_conv": cmd_create_conv,
        "conversations": cmd_conversations,
        "conversation_detail": cmd_conversation_detail,
        "interrupt": cmd_interrupt,
        "evaluate": cmd_evaluate,
        "share": cmd_share,
        "share_to_km": cmd_share_to_km,
        "projects": cmd_projects,
        "project_info": cmd_project_info,
        "fetch_data": cmd_fetch_data,
        "history": cmd_history,
        "user_info": cmd_user_info,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
