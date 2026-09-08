#!/usr/bin/env python3
"""
BA-Agent SSO 浏览器登录 — 通过 Playwright 打开本地浏览器登录页面，
等待用户登录后提取 cookie 中的 access_token。

作为 MOA 和 CIBA 均失败时的第三种降级鉴权方式，适用于 Claude Code 等本地桌面环境。
按操作系统自动选择可用浏览器：macOS(Chrome→WebKit→Chromium)、Windows(Chrome→Edge→Chromium)、Linux(Chrome→Firefox→Chromium)。

环境与页面对应关系：
  - test:  https://ba-ai.bi.test.sankuai.com/  → cookie: 0813b1648b_ssoid
  - st:    https://ba-ai.bi.st.sankuai.com/    → cookie: 07b8be90c9_ssoid
  - prod:  https://ba-ai.sankuai.com/          → cookie: 07b8be90c9_ssoid

用法:
    python3 inject-ba-cookie-playwright.py <misId> [--env prod|st|test] [--timeout 300]

环境变量：
    BA_ENV      - 目标环境（prod/st/test），默认 prod。CLI --env 参数优先级更高。
    BA_DATA_DIR - 运行时状态目录，默认 ~/.cache/ba-analysis
                  （Windows 默认 %LOCALAPPDATA%\\ba-analysis）

退出码：
    0  鉴权成功
    1  失败（浏览器启动失败 / 超时 / playwright 不可用）
"""

import argparse
import os
import sys
import time
from pathlib import Path

# ─── 跨平台编码兜底（Windows 控制台默认 cp936，防止中文/emoji 乱码） ──
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ─── 环境配置 ──────────────────────────────────────────────

ENV_CONFIG = {
    "test": {
        "url": "https://ba-ai.bi.test.sankuai.com/",
        "cookie_name": "0813b1648b_ssoid",
    },
    "st": {
        "url": "https://ba-ai.bi.st.sankuai.com/",
        "cookie_name": "07b8be90c9_ssoid",
    },
    "staging": {
        "url": "https://ba-ai.bi.st.sankuai.com/",
        "cookie_name": "07b8be90c9_ssoid",
    },
    "prod": {
        "url": "https://ba-ai.sankuai.com/",
        "cookie_name": "07b8be90c9_ssoid",
    },
}

# Token 存储目录（与 moa/ciba 脚本保持一致）
def _default_data_dir() -> Path:
    """按 OS 返回默认运行时状态目录（与 call_ba_agent.py 一致）。
    Windows → %LOCALAPPDATA%\\ba-analysis；其他 → ~/.cache/ba-analysis
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "ba-analysis"
    return Path.home() / ".cache" / "ba-analysis"


BA_DATA_DIR = Path(os.environ.get("BA_DATA_DIR") or _default_data_dir())

# ─── 结构化失败原因回传（供 call_ba_agent.py 埋点使用） ──────────
# 由调用方（_reauth()）通过环境变量 BA_AUTH_ERROR_FILE 传入一个临时文件路径；
# 本脚本在失败退出前把机器可读的错误码写入该文件（单行），不影响原有的
# 面向用户的中文 print 输出。调用方读取后用于埋点 error_reason 字段。
_ERROR_FILE = os.environ.get("BA_AUTH_ERROR_FILE")


def _report_error(code: str):
    """将错误码写入 BA_AUTH_ERROR_FILE（若设置），失败时静默，不影响主流程"""
    if not _ERROR_FILE:
        return
    try:
        Path(_ERROR_FILE).write_text(code, encoding="utf-8")
    except Exception:
        pass


# ─── Playwright 依赖检查 ───────────────────────────────────


def _ensure_playwright():
    """确保 playwright 已安装并可用。"""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        print("⚠️  playwright 未安装，正在安装...", file=sys.stderr)
        ret = os.system(f"{sys.executable} -m pip install playwright -q -i http://pypi.sankuai.com/simple --trusted-host pypi.sankuai.com")
        if ret != 0:
            # 内网源失败，尝试公网
            os.system(f"{sys.executable} -m pip install playwright -q")
        try:
            from playwright.sync_api import sync_playwright
            return sync_playwright
        except ImportError:
            print("❌ playwright 安装失败，请手动执行: pip install playwright", file=sys.stderr)
            _report_error("playwright_pkg_install_failed")
            sys.exit(1)


# ─── 主流程 ────────────────────────────────────────────────


def browser_login(mis="", env="prod", timeout=300):
    """
    打开浏览器让用户登录，登录成功后提取 cookie 中的 access_token。

    Args:
        mis: 用户 MIS ID（用于记录，非必须）
        env: 环境名（prod/st/test）
        timeout: 等待登录的超时秒数（默认 300）

    Returns:
        access_token 字符串

    退出码 1 表示失败。
    """
    config = ENV_CONFIG.get(env)
    if not config:
        print(f"❌  不支持的环境: {env}，可选: {list(ENV_CONFIG.keys())}", file=sys.stderr)
        _report_error("playwright_invalid_env")
        sys.exit(1)

    url = config["url"]
    cookie_name = config["cookie_name"]

    sync_playwright = _ensure_playwright()

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", file=sys.stderr)
    print("🌐  BA-Agent Playwright 浏览器登录", file=sys.stderr)
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", file=sys.stderr)
    print(f"  环境: {env}", file=sys.stderr)
    print(f"  页面: {url}", file=sys.stderr)
    print(f"  目标 Cookie: {cookie_name}", file=sys.stderr)
    if mis:
        print(f"  用户: {mis}", file=sys.stderr)
    print(f"  超时: {timeout}秒", file=sys.stderr)
    print("", file=sys.stderr)
    print("📌  浏览器即将打开，请在页面中完成登录...", file=sys.stderr)
    print("", file=sys.stderr)

    try:
        with sync_playwright() as p:
            browser = None
            # 按操作系统选择降级顺序
            if sys.platform == "darwin":
                # macOS: Chrome → WebKit → Chromium
                candidates = [
                    (p.chromium, "chrome", "Chrome"),
                    (p.webkit, None, "WebKit (内置)"),
                    (p.chromium, None, "Chromium (内置)"),
                ]
            elif sys.platform == "win32":
                # Windows: Chrome → Edge → Chromium
                candidates = [
                    (p.chromium, "chrome", "Chrome"),
                    (p.chromium, "msedge", "Edge"),
                    (p.chromium, None, "Chromium (内置)"),
                ]
            else:
                # Linux/其他: Chrome → Firefox → Chromium
                candidates = [
                    (p.chromium, "chrome", "Chrome"),
                    (p.firefox, None, "Firefox (内置)"),
                    (p.chromium, None, "Chromium (内置)"),
                ]
            for engine, channel, label in candidates:
                try:
                    kwargs = {"headless": False}
                    if channel:
                        kwargs["channel"] = channel
                    browser = engine.launch(**kwargs)
                    print(f"✅  已启动浏览器: {label}", file=sys.stderr)
                    break
                except Exception:
                    print(f"  ⚠️  {label} 不可用，尝试下一个...", file=sys.stderr)
                    continue

            if not browser:
                print("❌  无可用浏览器。请安装 Chrome/Edge，或执行: playwright install chromium", file=sys.stderr)
                _report_error("playwright_no_browser")
                sys.exit(1)

            context = browser.new_context()
            page = context.new_page()
            page.goto(url)

            print(f"⏳  等待登录完成（检测 cookie: {cookie_name}）...", file=sys.stderr)

            start_time = time.time()
            token = None

            while time.time() - start_time < timeout:
                cookies = context.cookies()
                for cookie in cookies:
                    if cookie["name"] == cookie_name and cookie.get("value"):
                        token = cookie["value"]
                        break
                if token:
                    break
                time.sleep(2)

            browser.close()
    except KeyboardInterrupt:
        print("\n⚠️  用户中断", file=sys.stderr)
        _report_error("playwright_user_interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"❌  Playwright 执行异常: {e}", file=sys.stderr)
        _report_error("playwright_exception")
        sys.exit(1)

    if not token:
        print(f"❌  等待 {timeout} 秒后仍未检测到 cookie {cookie_name}，登录可能未完成", file=sys.stderr)
        _report_error("playwright_login_timeout")
        sys.exit(1)

    # 保存 token（与 moa/ciba 脚本写入相同路径）
    BA_DATA_DIR.mkdir(parents=True, exist_ok=True)

    token_file = BA_DATA_DIR / "access_token.txt"
    token_file.write_text(token)
    print(f"✅  access_token 已保存: {token_file}", file=sys.stderr)

    # 保存 misId（供后续自动重鉴权时读取）
    if mis:
        mis_file = BA_DATA_DIR / "mis_id.txt"
        mis_file.write_text(mis)
        print(f"✅  misId 已保存: {mis_file}", file=sys.stderr)

    print("", file=sys.stderr)
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", file=sys.stderr)
    print("🎉  Playwright 浏览器鉴权完成！", file=sys.stderr)
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", file=sys.stderr)

    return token


# ─── CLI 入口 ─────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="BA-Agent Playwright 浏览器登录（MOA/CIBA 均失败时的降级方式）")
    parser.add_argument("mis", nargs="?", default="",
                        help="用户 MIS ID（可选，用于记录）")
    parser.add_argument("--env", default=None,
                        choices=["prod", "st", "staging", "test"],
                        help="环境: prod(默认)/st/test，未指定时读取 BA_ENV 环境变量")
    parser.add_argument("--timeout", type=int, default=300,
                        help="等待登录的超时秒数（默认 300）")

    args = parser.parse_args()

    # 环境优先级：--env 参数 > BA_ENV 环境变量 > 默认 prod
    env = args.env or os.environ.get("BA_ENV", "prod")

    browser_login(mis=args.mis, env=env, timeout=args.timeout)


if __name__ == "__main__":
    main()
