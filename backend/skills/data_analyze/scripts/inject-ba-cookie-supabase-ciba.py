#!/usr/bin/env python3
"""
BA-Agent CIBA（大象扫码）鉴权脚本（跨平台 Python 版）

逻辑：
  1. 从环境变量读取 IDENTIFIER（应用标识）
  2. 调用 ciba-auth 接口发起扫码授权，获取 auth_req_id
  3. 轮询 ciba-token 接口（每 5s、最多 3 分钟）直到用户扫码确认
  4. 换票（exchange-token-by-client-ids）
  5. 保存 access_token.txt / mis_id.txt / cookies.json

用法:
  python3 inject-ba-cookie-supabase-ciba.py <misId>

退出码：
  0  鉴权成功
  1  失败

环境变量：
  IDENTIFIER  - 应用标识（必填）
  BA_ENV      - 目标环境（prod/st/staging），默认 prod（test 环境不支持）
  BA_DATA_DIR - 运行时状态目录，默认 ~/.cache/ba-analysis
                （Windows 默认 %LOCALAPPDATA%\\ba-analysis）
"""

import json
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

# ─── 配置 ────────────────────────────────────────────────
BA_CLIENT_ID = "07b8be90c9"

_BA_ENV = os.environ.get("BA_ENV", "prod").lower()
BASE_URL = "https://supabase.sankuai.com"

def _default_data_dir() -> Path:
    """按 OS 返回默认运行时状态目录（与 call_ba_agent.py 一致）。
    Windows → %LOCALAPPDATA%\\ba-analysis；其他 → ~/.cache/ba-analysis
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "ba-analysis"
    return Path.home() / ".cache" / "ba-analysis"


BA_DATA_DIR = Path(os.environ.get("BA_DATA_DIR") or _default_data_dir())
BA_DATA_DIR.mkdir(parents=True, exist_ok=True)

ACCESS_TOKEN_OUTPUT = BA_DATA_DIR / "access_token.txt"
MIS_ID_PATH = BA_DATA_DIR / "mis_id.txt"
COOKIE_OUTPUT = BA_DATA_DIR / "cookies.json"

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


# ─── IDENTIFIER 读取 ────────────────────────────────────

def _read_identifier() -> str:
    """
    读取 IDENTIFIER（应用标识）：仅从环境变量 IDENTIFIER 读取。
    失败时打印错误并 exit(1)
    """
    env_val = os.environ.get("IDENTIFIER", "").strip()
    if env_val:
        return env_val

    print(
        "❌  环境变量 IDENTIFIER 未设置，当前环境无法使用ciba登录。",
        file=sys.stderr,
    )
    _report_error("ciba_identifier_missing")
    sys.exit(1)


# ─── HTTP 工具 ────────────────────────────────────────────

def _ensure_requests():
    try:
        import requests
        return requests
    except ImportError:
        ret = os.system("pip3 install requests -q")
        if ret != 0:
            raise RuntimeError("pip3 install requests 失败，请手动安装后重试")
        import requests
        return requests


def _post(requests_module, path: str, payload: dict) -> dict:
    """向 BASE_URL 发 POST，返回解析后的 JSON dict"""
    url = BASE_URL + path
    resp = requests_module.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ─── 主流程 ───────────────────────────────────────────────

def main():
    # ── 参数 ──────────────────────────────────────────────
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        script = os.path.basename(sys.argv[0])
        print("❌  错误：请提供登录者 misId\n")
        print("使用方式：")
        print(f"  python3 {script} <misId>\n")
        print("示例：")
        print(f"  python3 {script} zhangsan")
        sys.exit(1)

    login_hint = sys.argv[1].strip()

    print("━" * 40)
    print("🚀  开始获取 BA-Agent Token（CIBA 大象扫码）")
    print("━" * 40)
    print(f"环境: {_BA_ENV}  SSO: {BASE_URL}")
    print(f"login_hint: {login_hint}")
    print()

    requests = _ensure_requests()

    # ── 读取 IDENTIFIER ────────────────────────────────────
    identifier = _read_identifier()
    print(f"✅  IDENTIFIER: {identifier[:8]}...(已隐藏)")
    print()

    # ── 步骤 1：获取 auth_req_id ───────────────────────────
    print("📌  步骤 1：获取 auth_req_id...")
    try:
        bc_resp = _post(requests, "/api/sandbox/sso/ciba-auth", {
            "identifier": identifier,
            "misId": login_hint,
        })
    except Exception as e:
        print(f"❌  ciba-auth 请求失败: {e}")
        _report_error("ciba_auth_request_failed")
        sys.exit(1)

    print(f"  响应: {json.dumps(bc_resp, ensure_ascii=False)[:200]}")

    auth_req_id = ""
    if bc_resp.get("code") == 0:
        d = bc_resp.get("data") or {}
        auth_req_id = d.get("authReqId") or d.get("existingAuthReqId") or ""

    if not auth_req_id:
        d = bc_resp.get("data") or {}
        err = d.get("errorDescription") or bc_resp.get("message") or "未知错误"
        print(f"❌  auth_req_id 获取失败: {err}")
        _report_error("ciba_auth_req_id_failed")
        sys.exit(1)

    print(f"✅  auth_req_id: {auth_req_id}")
    print()

    # ── 步骤 2：轮询 access_token ──────────────────────────
    print("📌  步骤 2：轮询获取 access_token（每 5s 一次，最多 3 分钟）...")
    print("  ⏳ 请在大象 APP 中确认授权...\n")

    max_retry = 36
    access_token = ""
    for attempt in range(1, max_retry + 1):
        print(f"  第 {attempt}/{max_retry} 次轮询...")
        try:
            token_resp = _post(requests, "/api/sandbox/sso/ciba-token", {
                "authReqId": auth_req_id,
            })
        except Exception as e:
            print(f"  ⚠️  请求异常: {e}，等待 5s...")
            time.sleep(5)
            continue

        print(f"  响应: {json.dumps(token_resp, ensure_ascii=False)[:100]}...")

        if token_resp.get("code") == 0:
            access_token = (token_resp.get("data") or {}).get("accessToken", "") or ""

        if access_token:
            print("✅  access_token 获取成功")
            break

        d = token_resp.get("data") or {}
        err_desc = d.get("errorDescription") or token_resp.get("message") or ""
        if err_desc and err_desc != "null":
            print(f"  错误: {err_desc}")

        if attempt < max_retry:
            print("  等待 5s...")
            time.sleep(5)

    if not access_token:
        print("❌  轮询超时，未获取到 access_token（3 分钟内未确认授权）")
        _report_error("ciba_confirm_timeout")
        sys.exit(1)

    print()

    # ── 步骤 3：换票 ────────────────────────────────────────
    print("📌  步骤 3：换票获取最终 access-token...")
    try:
        exchange_resp = _post(requests, "/api/sandbox/sso/exchange-token-by-client-ids", {
            "accessToken": access_token,
            "clientIds": [BA_CLIENT_ID],
        })
    except Exception as e:
        print(f"❌  换票请求失败: {e}")
        _report_error("ciba_exchange_request_failed")
        sys.exit(1)

    print(f"  响应: {json.dumps(exchange_resp, ensure_ascii=False)[:200]}")

    raw_data = exchange_resp.get("data")
    if not raw_data or raw_data == [] or raw_data == "null":
        err = exchange_resp.get("message") or "未知错误"
        print(f"❌  换票失败: {err}")
        _report_error("ciba_exchange_failed")
        sys.exit(1)

    # ── 步骤 4：提取并保存 access_token ────────────────────
    print("📌  步骤 4：保存 access-token...")

    token_value = None
    cookie_data = []

    if isinstance(raw_data, str):
        # 直接就是 token 字符串
        token_value = raw_data
        cookie_data = [{
            "domain": "ba-ai.sankuai.com",
            "httpOnly": True,
            "name": "ssoid",
            "path": "/",
            "secure": True,
            "value": raw_data,
        }]
    elif isinstance(raw_data, list):
        cookie_data = raw_data
        for c in raw_data:
            name = c.get("name", "").lower()
            if "ssoid" in name or "token" in name:
                token_value = c["value"]
                break
        if not token_value and raw_data:
            token_value = raw_data[0]["value"]
    else:
        print(f"❌  换票响应格式未知: {type(raw_data)}")
        _report_error("ciba_exchange_format_unknown")
        sys.exit(1)

    if not token_value:
        print("❌  无法从换票响应中提取 token 值")
        _report_error("ciba_token_extract_failed")
        sys.exit(1)

    # 写入 access_token.txt
    ACCESS_TOKEN_OUTPUT.write_text(token_value, encoding="utf-8")
    print(f"✅  access-token 已保存: {ACCESS_TOKEN_OUTPUT}")

    # 写入 mis_id.txt（供子 agent 沙箱读取）
    if login_hint:
        MIS_ID_PATH.write_text(login_hint, encoding="utf-8")
        print(f"✅  misId 已保存: {MIS_ID_PATH}")

    # 写入 cookies.json（供其他工具使用）
    COOKIE_OUTPUT.write_text(
        json.dumps(cookie_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"✅  cookies.json 已生成: {COOKIE_OUTPUT}")

    print()
    print("━" * 40)
    print("🎉  BA-Agent 鉴权完成！")
    print("━" * 40)


if __name__ == "__main__":
    main()
