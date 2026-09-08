#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BA-Agent CatDesk 浏览器鉴权脚本（跨平台 Python 版）

逻辑：
  1. 检查 catdesk CLI 是否可用（CatDesk 内置浏览器命令）
  2. 导航到 BA-Agent 页面（自动复用用户已登录的 SSO 会话）
  3. 获取页面 cookies，提取 ssoid（即 access_token）
  4. 将 token 写入 BA_DATA_DIR/access_token.txt
  5. 将 misId 写入 BA_DATA_DIR/mis_id.txt
  6. （可选）调用 BA-Agent user_info 接口验证 token 有效性

适用场景：
  - catpaw agent 环境（CatDesk 内置浏览器可复用 SSO 登录态）
  - 运行在没有 Node.js 的环境中（MOA 鉴权不可用）
  - CatDesk 已安装且用户已在大象/App 中登录过

用法:
  python3 inject-ba-cookie-catdesk.py [misId] [--env prod|st|test] [--timeout 30]
  Windows 下把 python3 换成 python（详见 SKILL.md 核心约束第 0 条）

退出码：
  0  鉴权成功
  1  失败

环境变量：
  BA_ENV      - 目标环境（prod/st/test），默认 prod
  BA_DATA_DIR - 运行时状态目录，默认 ~/.cache/ba-analysis
                （Windows 默认 %LOCALAPPDATA%\\ba-analysis）
"""

# 惰性注解：使 list[str] / str | None 等类型注解在 Python 3.7+ 均可用
# （避免在旧版本 Python 上因 PEP 585 / PEP 604 语法报错）
from __future__ import annotations

import os
import sys

# ─── 跨平台编码兜底 ───────────────────────────────────────
# Windows 控制台默认 cp936(GBK)，直接 print emoji/中文会乱码或抛
# UnicodeEncodeError。统一把 stdout/stderr 重配为 utf-8（与本 skill
# 其他脚本保持一致的 reconfigure 写法）。
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # Python < 3.7 兜底
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


import argparse       # 用于解析命令行参数（misId、--env、--timeout）
import json           # 用于解析 catdesk 返回的 JSON 输出
import subprocess     # 用于调用外部命令（catdesk CLI）
import time           # 用于等待页面加载
from pathlib import Path  # 用于跨平台路径操作（Windows/macOS/Linux 通用）


# ═══════════════════════════════════════════════════════════════
# 环境配置
# ═══════════════════════════════════════════════════════════════
# ENV_CONFIG 定义了不同环境的 BA-Agent 访问地址和对应的 cookie 名称。
ENV_CONFIG = {
    "test": {
        "url": "https://ba-ai.bi.test.sankuai.com/",
        "cookie_name": "0813b1648b_ssoid",  # test 环境的 ssoid cookie 名称
    },
    "st": {
        "url": "https://ba-ai.bi.st.sankuai.com/",
        "cookie_name": "07b8be90c9_ssoid",  # staging 环境的 ssoid cookie 名称
    },
    "staging": {
        "url": "https://ba-ai.bi.st.sankuai.com/",
        "cookie_name": "07b8be90c9_ssoid",
    },
    "prod": {
        "url": "https://ba-ai.sankuai.com/",
        "cookie_name": "07b8be90c9_ssoid",  # 生产环境的 ssoid cookie 名称
    },
}

# BA_CLIENT_ID 是 BA-Agent 在美团 SSO 体系中的客户端标识，用于 user_info 验证
BA_CLIENT_ID = "07b8be90c9"


def _default_data_dir() -> str:
    """按 OS 返回默认运行时状态目录（与 call_ba_agent.py 的 _default_data_dir 一致）。
    Windows → %LOCALAPPDATA%\\ba-analysis；其他 → ~/.cache/ba-analysis
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ba-analysis")
    return os.path.expanduser("~/.cache/ba-analysis")


# 从环境变量读取 BA_DATA_DIR；未指定时按 OS 决定默认目录
BA_DATA_DIR = Path(os.environ.get("BA_DATA_DIR") or _default_data_dir())

# ─── 结构化失败原因回传（供 call_ba_agent.py 埋点使用） ──────────
# 由调用方（_reauth()）通过环境变量 BA_AUTH_ERROR_FILE 传入一个临时文件路径；
# 本脚本在失败退出前把机器可读的错误码写入该文件（单行），不影响原有的
# 面向用户的中文 print 输出。调用方读取后用于埋点 error_reason 字段。
_ERROR_FILE = os.environ.get("BA_AUTH_ERROR_FILE")


def _report_error(code: str) -> None:
    """将错误码写入 BA_AUTH_ERROR_FILE（若设置），失败时静默，不影响主流程"""
    if not _ERROR_FILE:
        return
    try:
        Path(_ERROR_FILE).write_text(code, encoding="utf-8")
    except Exception:
        pass

# 定义 token 和 misId 的输出文件路径
ACCESS_TOKEN_OUTPUT = BA_DATA_DIR / "access_token.txt"
MIS_ID_PATH = BA_DATA_DIR / "mis_id.txt"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _catdesk_cmd_candidates() -> list[str]:
    """
    返回 catdesk 命令的候选列表。

    Windows 上 catdesk 实际安装为 .cmd 文件（catdesk.cmd），
    subprocess.run(shell=False) 无法直接执行 .ps1，
    因此 Windows 下优先尝试 catdesk.cmd。
    """
    if sys.platform == "win32":
        return ["catdesk.cmd", "catdesk"]
    return ["catdesk"]


def _catdesk_available() -> bool:
    """
    检查 catdesk CLI 是否可用。

    原理：尝试执行 "catdesk --version"（Windows 下先尝试 catdesk.cmd），
    如果返回码为 0 则认为可用。
    """
    for cmd in _catdesk_cmd_candidates():
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                text=True,
                shell=False,
                encoding="utf-8",   # 避免 Windows 下按 cp936 strict 解码抛 UnicodeDecodeError
                errors="replace",
            )
            if result.returncode == 0:
                return True
        except (FileNotFoundError, OSError):
            continue
    return False


def _catdesk_browser_action(action_json: dict) -> dict:
    """
    调用 catdesk browser-action 命令，返回解析后的 JSON 结果。

    参数:
        action_json: dict 类型的浏览器动作，例如 {"action": "navigate", "url": "..."}

    返回:
        解析后的 JSON dict。如果执行失败，返回 {"success": False, "error": "..."}
    """
    # 将 dict 转为 JSON 字符串，ensure_ascii=False 保证中文不被转义
    json_str = json.dumps(action_json, ensure_ascii=False)

    # 遍历候选命令，优先尝试可用的那个
    last_error = ""
    for cmd in _catdesk_cmd_candidates():
        try:
            result = subprocess.run(
                [cmd, "browser-action", json_str],
                capture_output=True,
                text=True,
                shell=False,
                encoding="utf-8",   # 强制以 UTF-8 解码输出，避免 Windows 下 GBK 乱码
                errors="replace",
            )
        except FileNotFoundError as exc:
            last_error = str(exc)
            continue  # 尝试下一个候选命令

        # 如果命令本身执行失败（如 catdesk 报错退出），将 stderr 作为错误信息返回
        if result.returncode != 0:
            return {"success": False, "error": (result.stderr or "").strip() or f"catdesk 退出码 {result.returncode}"}

        # 解析 stdout 中的 JSON 输出
        stdout = (result.stdout or "").strip()
        if not stdout:
            return {"success": False, "error": "catdesk 返回空输出"}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"无法解析 catdesk 输出: {exc}\n原始输出: {stdout[:500]}"}

    # 所有候选命令都不可用
    return {"success": False, "error": f"catdesk 命令未找到，请确认 CatDesk 已安装。最后一次错误: {last_error}"}


def _navigate_to_ba_agent(env: str, timeout: int = 30) -> bool:
    """
    使用 catdesk 浏览器导航到 BA-Agent 页面。

    原理:
        catdesk 浏览器集成了大象/SSO 的登录态，因此导航到 ba-ai.sankuai.com 时
        会自动携带已登录用户的 cookie，无需手动输入用户名密码。
    """
    url = ENV_CONFIG[env]["url"]
    print(f"  正在导航到 {url} ...")

    resp = _catdesk_browser_action({
        "action": "navigate",
        "url": url,
        "waitUntil": "networkidle",
    })

    if not resp.get("success"):
        print(f"  ❌  导航失败: {resp.get('error')}")
        return False

    print(f"  ✅  导航成功: {resp.get('data', {}).get('title', 'Unknown')}")
    return True


def _extract_ssoid_cookie(env: str) -> str | None:
    """
    从 catdesk 浏览器中获取指定环境的 ssoid cookie。

    返回:
        ssoid cookie 的 value 字符串。如果未找到或执行失败，返回 None。
    """
    cookie_name = ENV_CONFIG[env]["cookie_name"]
    print(f"  正在提取 cookie: {cookie_name} ...")

    resp = _catdesk_browser_action({"action": "cookies_get"})

    if not resp.get("success"):
        print(f"  ❌  获取 cookies 失败: {resp.get('error')}")
        return None

    cookies = resp.get("data", {}).get("cookies", [])
    if not cookies:
        print("  ⚠️  当前页面没有 cookies，可能未登录")
        return None

    # 遍历所有 cookie，查找匹配的 ssoid cookie
    for cookie in cookies:
        if cookie.get("name") == cookie_name:
            token = cookie.get("value", "")
            if token:
                safe_preview = token[:20] + "..." + token[-10:] if len(token) > 30 else token[:10] + "..."
                print(f"  ✅  成功提取 token（预览）: {safe_preview}")
                return token

    # 如果主 cookie 没找到，也尝试查找 duolaam_ssoid（BA-Agent 的备用 cookie）
    for cookie in cookies:
        if cookie.get("name") == "duolaam_ssoid":
            token = cookie.get("value", "")
            if token:
                safe_preview = token[:20] + "..." + token[-10:] if len(token) > 30 else token[:10] + "..."
                print(f"  ✅  从 duolaam_ssoid 提取 token（预览）: {safe_preview}")
                return token

    print(f"  ⚠️  未找到 cookie '{cookie_name}'，当前 cookies 列表:")
    for cookie in cookies:
        print(f"      - {cookie.get('name')} (domain: {cookie.get('domain')})")
    return None


def _build_cookie_header(token: str) -> str:
    """
    构造 Cookie header，与 call_ba_agent.py 保持一致。
    """
    return f"07b8be90c9_ssoid={token}"


def _verify_token(token: str, mis_id: str) -> bool:
    """
    调用 BA-Agent getUserInfo 接口验证 token 是否有效。

    返回:
        True 表示验证通过，False 表示验证失败（网络/接口异常视为跳过）。
    """
    print("  正在验证 token 有效性 ...")

    try:
        import requests
    except ImportError:
        print("  ⚠️  缺少 requests 模块，跳过验证（建议安装: pip install requests）")
        return True  # 缺少依赖时跳过验证，不阻塞主流程

    env = os.environ.get("BA_ENV", "prod").lower()
    base_url = {
        "test": "https://ba-ai.bi.test.sankuai.com",
        "st": "https://ba-ai.bi.st.sankuai.com",
        "staging": "https://ba-ai.bi.st.sankuai.com",
        "prod": "https://ba-ai.sankuai.com",
    }.get(env, "https://ba-ai.sankuai.com")

    headers = {
        "access-token": token,
        "Content-Type": "application/json",
        "X-Source": "catclaw",
        "Cookie": _build_cookie_header(token),
    }

    try:
        resp = requests.get(
            f"{base_url}/baagent/api/v2/platform/getUserInfo",
            headers=headers,
            timeout=15,
            allow_redirects=False,
        )
    except Exception as exc:
        print(f"  ⚠️  验证请求异常: {exc}，跳过验证")
        return True  # 网络异常不视为 token 无效

    # 检查是否被重定向到 SSO 登录页（token 过期的典型表现）
    if resp.status_code in (301, 302, 307, 308):
        print(f"  ❌  Token 已过期（被重定向到登录页，status={resp.status_code}）")
        return False

    try:
        data = resp.json()
    except Exception:
        print(f"  ⚠️  响应解析失败（status={resp.status_code}），跳过验证")
        return True

    # 接口业务码判断：code == 0 表示成功
    if data.get("code") != 0:
        msg = data.get("msg") or "未知错误"
        print(f"  ❌  Token 验证失败: {msg}")
        return False

    user = data.get("data") or {}
    actual_mis = (
        user.get("misId")
        or user.get("mis_id")
        or user.get("loginName")
        or ""
    )
    actual_name = user.get("misName") or user.get("userName") or ""

    if actual_mis and mis_id and actual_mis.lower() != mis_id.lower():
        print(f"  ⚠️  MIS ID 不匹配: 期望 {mis_id}，实际 {actual_mis}")
        return False

    print(f"  ✅  Token 验证通过（用户: {actual_name}, MIS: {actual_mis}）")
    return True


def _save_token(token: str, mis_id: str) -> None:
    """
    将 token 和 misId 持久化到本地文件，供 call_ba_agent.py 读取。
    """
    BA_DATA_DIR.mkdir(parents=True, exist_ok=True)

    ACCESS_TOKEN_OUTPUT.write_text(token, encoding="utf-8")
    print(f"  ✅  Token 已保存: {ACCESS_TOKEN_OUTPUT}")

    MIS_ID_PATH.write_text(mis_id, encoding="utf-8")
    print(f"  ✅  MIS ID 已保存: {MIS_ID_PATH}")


def _clear_existing_session() -> None:
    """
    清理已有的 token 文件，避免旧 token 干扰。
    """
    if ACCESS_TOKEN_OUTPUT.exists():
        ACCESS_TOKEN_OUTPUT.unlink()
        print(f"  🗑️  已清理旧 token: {ACCESS_TOKEN_OUTPUT}")
    if MIS_ID_PATH.exists():
        MIS_ID_PATH.unlink()
        print(f"  🗑️  已清理旧 MIS ID: {MIS_ID_PATH}")


def _close_tab() -> None:
    """
    关闭当前浏览器标签页，避免鉴权完成后浏览器窗口残留。
    """
    resp = _catdesk_browser_action({"action": "tab_close"})
    if resp.get("success"):
        print("  ✅  已关闭浏览器标签页")
    else:
        print(f"  ⚠️  关闭标签页失败: {resp.get('error', '未知错误')}")


# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="通过 CatDesk 浏览器提取 BA-Agent SSO cookie 完成鉴权",
    )
    parser.add_argument(
        "mis_id",
        nargs="?",
        default="",
        help="你的美团 MIS ID（例如 wangting61），用于验证 token",
    )
    parser.add_argument(
        "--env",
        choices=["prod", "st", "staging", "test"],
        default=os.environ.get("BA_ENV", "prod").lower(),
        help="目标环境（默认从 BA_ENV 环境变量读取，否则为 prod）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="页面加载超时时间（秒），默认 30",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过 token 验证步骤",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="强制清理已有 token 后重新鉴权",
    )
    args = parser.parse_args()

    env = args.env
    mis_id = args.mis_id

    print("=" * 50)
    print("🚀  BA-Agent CatDesk 浏览器鉴权")
    print("=" * 50)
    print(f"  环境: {env}")
    print(f"  目标: {ENV_CONFIG[env]['url']}")
    print(f"  用户: {mis_id or '(未指定)'}")
    print()

    # ── 前置检查：catdesk CLI 是否可用 ───────────────────────
    print("[1/7] 检查 catdesk CLI 可用性 ...")
    if not _catdesk_available():
        print("  ❌  catdesk 命令不可用，请确认 CatDesk 已安装并加入 PATH")
        print()
        print("💡  提示: CatDesk 安装后通常会自动加入 PATH。")
        print("   如果在 PowerShell 中无法运行，尝试执行:")
        print("   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned")
        _report_error("catdesk_cli_unavailable")
        sys.exit(1)
    print("  ✅  catdesk CLI 可用")
    print()

    if args.clear:
        print("[1.5/7] 清理已有会话 ...")
        _clear_existing_session()
        print()

    # 如果未提供 mis_id，尝试从已有的 mis_id.txt 读取
    if not mis_id and MIS_ID_PATH.exists():
        mis_id = MIS_ID_PATH.read_text(encoding="utf-8").strip()
        print(f"  📖  从本地缓存读取 MIS ID: {mis_id}")

    # ── 导航到 BA-Agent ──────────────────────────────────────
    print("[2/7] 导航到 BA-Agent 页面 ...")
    if not _navigate_to_ba_agent(env, timeout=args.timeout):
        _report_error("catdesk_navigate_failed")
        sys.exit(1)
    print()

    # 等待页面 JavaScript 完全执行并完成 SSO 登录态同步
    print("[3/7] 等待页面稳定 ...")
    time.sleep(2)
    print("  ✅  页面已稳定")
    print()

    # ── 提取 ssoid cookie ────────────────────────────────────
    print("[4/7] 提取 SSO cookie ...")
    token = _extract_ssoid_cookie(env)
    if not token:
        print()
        print("❌  未能提取到有效的 access_token，可能原因:")
        print("   1. 你尚未在 CatDesk/大象中登录美团 SSO")
        print("   2. BA-Agent 页面加载失败或跳转到了登录页")
        print("   3. SSO cookie 已过期")
        print()
        print("💡  建议: 在 CatDesk 浏览器中手动访问 https://ba-ai.sankuai.com/")
        print('   确认能正常显示"商业分析助手"页面后再运行本脚本。')
        _report_error("catdesk_cookie_not_found")
        sys.exit(1)
    print()

    # ── 验证 token（可选）─────────────────────────────────────
    if not args.no_verify and mis_id:
        print("[5/7] 验证 token ...")
        verified = _verify_token(token, mis_id)
        if not verified:
            print("  ⚠️  验证未通过，但 token 仍会被保存（可能是接口临时异常）")
            print("  💡  保存后可手动运行 call_ba_agent.py user_info 再次验证")
        print()
    else:
        print("[5/7] 跳过 token 验证")
        print()

    # ── 保存 token ───────────────────────────────────────────
    print("[6/7] 保存鉴权凭证 ...")
    _save_token(token, mis_id or "unknown")
    print()

    # ── 关闭浏览器标签页 ───────────────────────────────────────
    print("[7/7] 关闭浏览器标签页 ...")
    _close_tab()
    print()

    print("=" * 50)
    print("✅  CatDesk 浏览器鉴权成功！")
    print("=" * 50)
    print()
    print("📋  凭证位置:")
    print(f"   Token : {ACCESS_TOKEN_OUTPUT}")
    print(f"   MIS ID: {MIS_ID_PATH}")
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()
