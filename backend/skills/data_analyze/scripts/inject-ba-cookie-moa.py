#!/usr/bin/env python3
"""
BA-Agent MOA 本地鉴权脚本（跨平台 Python 版）
逻辑：
  1. 探测本地 MOA 是否可用（npx mtsso-moa-feature-probe）
  2. 通过 MOA 换票（npx mtsso-moa-local-exchange --audience <client_id>）
  3. 成功 → token 写入 BA_DATA_DIR/access_token.txt，退出码 0
  4. 任一步骤失败 → 退出码 1（调用方降级 CIBA）

用法: python3 inject-ba-cookie-moa.py

退出码：
  0  鉴权成功
  1  失败（调用方应降级到 inject-ba-cookie-supabase-ciba.py CIBA 方式）

环境变量：
  BA_DATA_DIR - 运行时状态目录，默认 ~/.cache/ba-analysis
                （Windows 默认 %LOCALAPPDATA%\\ba-analysis）
"""

import base64
import json
import os
import shutil
import subprocess
import sys
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
NPM_REGISTRY = "http://r.npm.sankuai.com"

# Windows 下 npm/npx 实为 npx.cmd，subprocess(shell=False) 直接调 "npx"
# 会 FileNotFoundError，需用带扩展名的命令。
NPX_CMD = "npx.cmd" if sys.platform == "win32" else "npx"

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


def _run(cmd, **kwargs):
    """运行命令，返回 (returncode, stdout, stderr)"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **kwargs,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def _npx_available():
    """检查 npx 是否可用"""
    rc, _, _ = _run([NPX_CMD, "--version"])
    return rc == 0


def _check_mtsso():
    """检测 mtsso-moa-feature-probe 是否可用"""
    # 全局命令存在即可；该工具执行 --help 时会正常返回退出码 2。
    if shutil.which("mtsso-moa-feature-probe"):
        return True
    # 再通过 npx --no-install 探测（不自动安装），接受帮助信息的退出码 2。
    rc, _, _ = _run([NPX_CMD, "--no-install", "mtsso-moa-feature-probe", "--help"])
    return rc in (0, 2)


def main():
    print("━" * 40)
    print("🚀  BA-Agent MOA 本地鉴权")
    print("━" * 40)
    print()

    # ── 前置：检查 npx ────────────────────────────────────
    if not _npx_available():
        print("❌  未检测到 npx，请先安装 Node.js（https://nodejs.org）")
        _report_error("moa_npx_not_found")
        sys.exit(1)

    # ── 步骤 1：检查 mtsso 工具 ───────────────────────────
    print("📌  步骤 1：检查 mtsso 工具...")
    if not _check_mtsso():
        print()
        print("❌  未检测到 @mtfe/mtsso-auth-official，MOA 鉴权不可用。")
        print()
        print("  你可以选择：")
        print("  1. 手动安装后重试 MOA 鉴权：")
        print(f"     npm install @mtfe/mtsso-auth-official@latest --registry {NPM_REGISTRY}")
        print()
        print("  2. 改用 CIBA（大象扫码）方式登录：")
        print("     python3 inject-ba-cookie-supabase-ciba.py <你的misId>")
        _report_error("moa_tool_not_installed")
        sys.exit(1)
    print("✅  工具就绪")
    print()

    # ── 步骤 2：探测本地 MOA ──────────────────────────────
    print("📌  步骤 2：探测本地 MOA（mtsso-moa-feature-probe）...")
    probe_rc, probe_out, probe_err = _run([NPX_CMD, "mtsso-moa-feature-probe", "-t", "3"])

    probe_status = "unknown"
    probe_reason = ""
    try:
        probe_data = json.loads(probe_out)
        probe_status = probe_data.get("status", "unknown")
        probe_reason = probe_data.get("reason", "")
    except Exception:
        pass

    print(f"  探测退出码: {probe_rc}  状态: {probe_status}")
    if probe_reason:
        print(f"  详情: {probe_reason}")

    if probe_rc != 0:
        print()
        print(f"❌  本地 MOA 不可用（退出码: {probe_rc}，状态: {probe_status}）")
        print("  请确认 MOA 已启动并完成登录")
        _report_error("moa_probe_unavailable")
        sys.exit(1)

    print("✅  MOA 可用")
    print()

    # ── 步骤 3：通过 MOA 换票 ─────────────────────────────
    print(f"📌  步骤 3：本地换票（mtsso-moa-local-exchange --audience {BA_CLIENT_ID}）...")
    exchange_rc, exchange_out, exchange_err = _run(
        [NPX_CMD, "mtsso-moa-local-exchange", "--audience", BA_CLIENT_ID]
    )

    if exchange_rc != 0:
        error_msg = ""
        try:
            d = json.loads(exchange_out)
            error_msg = d.get("error", "") or d.get("message", "")
        except Exception:
            pass
        print(f"❌  换票失败（退出码: {exchange_rc}）")
        if error_msg:
            print(f"  错误: {error_msg}")
        if exchange_err:
            # 只打最后 5 行
            for line in exchange_err.splitlines()[-5:]:
                print(f"  详情: {line}")
        _report_error("moa_exchange_failed")
        sys.exit(1)

    # ── 步骤 4：提取并保存 access_token ──────────────────
    print("📌  步骤 4：保存 access_token...")

    access_token = ""
    try:
        token_data = json.loads(exchange_out)
        access_token = token_data.get("access_token", "")
    except Exception:
        pass

    if not access_token:
        print("❌  无法从响应中提取 access_token")
        print(f"  原始响应: {exchange_out[:200]}")
        _report_error("moa_token_extract_failed")
        sys.exit(1)

    ACCESS_TOKEN_OUTPUT.write_text(access_token, encoding="utf-8")
    print(f"✅  access_token 已保存: {ACCESS_TOKEN_OUTPUT}")

    # ── 从 token 中解析 misId 并缓存 ─────────────────────
    # token 格式：<encrypted>**mtsso**<sig>**<base64(uid,misId,name,email,...)>
    # 最后一段 base64 解码后为逗号分隔字段，[1] 即 misId
    mis_id = ""
    try:
        raw_parts = access_token.split("**")
        last = raw_parts[-1]
        padded = last + "=" * (4 - len(last) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        fields = decoded.split(",")
        if len(fields) >= 2:
            mis_id = fields[1].strip()
    except Exception:
        pass

    if mis_id and mis_id != "null":
        MIS_ID_PATH.write_text(mis_id, encoding="utf-8")
        print(f"✅  misId 已从 token 解析并缓存: {mis_id}")

    print()
    print("━" * 40)
    print("🎉  MOA 本地鉴权完成！")
    print("━" * 40)


if __name__ == "__main__":
    main()
