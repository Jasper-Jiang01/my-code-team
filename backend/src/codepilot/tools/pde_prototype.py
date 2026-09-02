"""点评 PDE Agent：页面原型图 / 设计稿工具。

操作指南：https://km.sankuai.com/collabpage/2776444575

有页面原型、设计稿或高保真 Demo 需求时使用。优先调用配置的 PDE/DF
HTTP 适配层；未配置时在本地产出可截图的移动端原型 HTML，并返回 DF /
Multica / Picasso 入口，便于继续走真实 Picasso 工程与真机验证。
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import tool

from codepilot.core.config import settings, writable_artifacts_dir

logger = logging.getLogger(__name__)

_GUIDE_URL = "https://km.sankuai.com/collabpage/2776444575"
_DF_URL = "https://dfs.sankuai.com/agents?workspaceId=270&group=label"
_MULTICA_URL = (
    "https://mlt.sankuai.com/ccbb/templates/"
    "82721cdd-8075-47e1-9848-68f36d27826b?type=squad&page=2"
)
_PICASSO_URL = "https://picasso.sankuai.com/portalnew/pages"

_STAGES = ("requirements", "ideation", "design", "code", "device")
_STAGE_ALIASES = {
    "需求分析": "requirements",
    "方案发散": "ideation",
    "设计稿": "design",
    "设计稿绘制": "design",
    "原型": "design",
    "原型图": "design",
    "页面原型": "design",
    "代码": "code",
    "代码交付": "code",
    "真机": "device",
    "真机验证": "device",
}

_DEFAULT_COMPONENTS = ("导航栏", "主内容区", "操作按钮", "底栏 Tab")


class PDEPrototypeError(RuntimeError):
    """当 PDE 原型生成失败时抛出。"""


def _launch() -> dict[str, str]:
    return {
        "guide": _GUIDE_URL,
        "df": _DF_URL,
        "multica": _MULTICA_URL,
        "picasso": _PICASSO_URL,
    }


def _playbook(stage: str, app_key: str, repo: str, ones_url: str) -> str:
    target = app_key or repo or "在 Picasso 门户搜索目标页面，复制仓库或 AppKey"
    ones = ones_url or "无 ONES 则在 DF 创建任务时勾选关联并填写需求描述"
    steps = {
        "requirements": "先做需求分析：明确增量、边界与约束，再决定是否进入方案发散。",
        "ideation": "需求较模糊时先方案发散，对比 2-3 个方向后再锁定设计稿。",
        "design": "产出页面原型图/设计稿；有 Magics 稿可直接丢 MG 链接还原。",
        "code": "进入真实 Picasso 工程改代码，不要只生成独立 HTML Demo。",
        "device": "连接真机 LiveLoad，打开页面、截图并校验样式与跳转。",
    }
    return (
        f"{steps.get(stage, steps['design'])} "
        f"入口：DF {_DF_URL} 或 Multica {_MULTICA_URL}。"
        f"代码目标：{target}。ONES：{ones}。"
        f"完整操作见 {_GUIDE_URL}"
    )


def _normalize_stage(stage: str) -> str:
    raw = (stage or "design").strip()
    mapped = _STAGE_ALIASES.get(raw) or _STAGE_ALIASES.get(raw.lower())
    if mapped:
        return mapped
    lowered = raw.lower()
    return lowered if lowered in _STAGES else "design"


def _safe_filename(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (text or "")[:32]).strip()
    return cleaned or "page_prototype"


def _clip_items(items: list[str] | None, limit: int = 12) -> list[str]:
    cleaned: list[str] = []
    for item in items or []:
        text = re.sub(r"\s+", " ", str(item)).strip()
        if text:
            cleaned.append(text[:40])
        if len(cleaned) >= limit:
            break
    return cleaned or list(_DEFAULT_COMPONENTS)


def _render_local_html(
    requirement: str,
    page_name: str,
    components: list[str],
    stage: str,
) -> str:
    title = html_lib.escape((page_name or requirement or "页面原型")[:40])
    brief = html_lib.escape((requirement or "未填写需求")[:160])
    cards = "".join(
        f"<li class='card'><span class='idx'>{index:02d}</span>"
        f"<span class='name'>{html_lib.escape(name)}</span></li>"
        for index, name in enumerate(components, start=1)
    )
    stage_label = {
        "requirements": "需求分析",
        "ideation": "方案发散",
        "design": "设计稿 / 原型图",
        "code": "代码交付",
        "device": "真机验证",
    }.get(stage, "设计稿 / 原型图")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ --brand:#ff6633; --bg:#f4f4f4; --ink:#111; --muted:#777; }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:#d8d8d8; color:var(--ink);
      font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",sans-serif;
    }}
    .phone {{
      width:375px; min-height:812px; margin:16px auto; background:var(--bg);
      border-radius:28px; overflow:hidden; box-shadow:0 12px 40px rgba(0,0,0,.18);
    }}
    .status {{ height:44px; background:#fff; display:flex; align-items:center;
      justify-content:space-between; padding:0 18px; font-size:13px; font-weight:600; }}
    header {{
      background:#fff; padding:10px 16px 14px; border-bottom:1px solid #eee;
    }}
    header h1 {{ margin:0; font-size:18px; }}
    .sub {{ margin:6px 0 0; color:var(--muted); font-size:12px; line-height:1.4; }}
    .badge {{
      display:inline-block; margin-top:8px; padding:2px 8px; border-radius:999px;
      background:#fff1eb; color:var(--brand); font-size:11px;
    }}
    ul.cards {{ list-style:none; margin:12px; padding:0; }}
    .card {{
      background:#fff; border-radius:12px; padding:14px 12px; margin-bottom:10px;
      display:flex; gap:10px; align-items:center; box-shadow:0 1px 3px rgba(0,0,0,.04);
    }}
    .idx {{
      width:28px; height:28px; border-radius:8px; background:#fff1eb; color:var(--brand);
      font-size:11px; display:flex; align-items:center; justify-content:center; font-weight:700;
    }}
    .name {{ font-size:14px; }}
    .cta {{
      margin:16px; height:44px; border:0; border-radius:22px; background:var(--brand);
      color:#fff; font-size:15px; width:calc(100% - 32px);
    }}
    nav {{
      height:56px; background:#fff; display:flex; border-top:1px solid #eee;
      position:sticky; bottom:0;
    }}
    nav span {{ flex:1; text-align:center; font-size:11px; color:#999; padding-top:18px; }}
    nav span.on {{ color:var(--brand); font-weight:600; }}
  </style>
</head>
<body>
  <div class="phone">
    <div class="status"><span>9:41</span><span>点评 PDE</span></div>
    <header>
      <h1>{title}</h1>
      <p class="sub">{brief}</p>
      <span class="badge">{html_lib.escape(stage_label)}</span>
    </header>
    <ul class="cards">{cards}</ul>
    <button class="cta" type="button">确认方案</button>
    <nav>
      <span class="on">首页</span><span>美食</span><span>评价</span><span>我的</span>
    </nav>
  </div>
</body>
</html>
"""


def _write_html(output_dir: Path, markup: str) -> Path:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "design.html"
        path.write_text(markup, encoding="utf-8")
        return path
    except OSError:
        logger.warning("pde_prototype: cannot write %s, falling back", output_dir)
        fallback = writable_artifacts_dir() / output_dir.name
        fallback.mkdir(parents=True, exist_ok=True)
        path = fallback / "design.html"
        path.write_text(markup, encoding="utf-8")
        return path


def _extract_remote_html(payload: dict[str, Any]) -> str:
    for key in ("html", "prototype_html", "markup"):
        value = payload.get(key)
        if isinstance(value, str) and "<" in value:
            return value
    result = payload.get("result")
    if isinstance(result, dict):
        return _extract_remote_html(result)
    return ""


def _try_remote(
    requirement: str,
    page_name: str,
    app_key: str,
    repo: str,
    ones_url: str,
    stage: str,
    components: list[str],
) -> dict[str, Any] | None:
    endpoint = (settings.pde_endpoint or "").strip()
    if not endpoint:
        return None
    timeout = max(8.0, float(settings.pde_timeout or 60.0))
    response = httpx.post(
        endpoint,
        json={
            "requirement": requirement,
            "page_name": page_name,
            "app_key": app_key,
            "repo": repo,
            "ones_url": ones_url,
            "stage": stage,
            "components": components,
            "guide": _GUIDE_URL,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise PDEPrototypeError("PDE endpoint returned a non-object JSON")
    return payload


def _local_result(
    requirement: str,
    page_name: str,
    stage: str,
    components: list[str],
    output_dir: Path,
    app_key: str,
    repo: str,
    ones_url: str,
) -> dict[str, Any]:
    markup = _render_local_html(requirement, page_name, components, stage)
    html_path = _write_html(output_dir, markup)
    return {
        "ok": True,
        "mode": "local_prototype",
        "unverified": True,
        "stage": stage,
        "html_path": str(html_path),
        "components": components,
        "layout": "mobile-375 点评风格卡片流 + 底栏 Tab",
        "interactions": ["确认方案", "底栏切换"],
        "launch": _launch(),
        "playbook": _playbook(stage, app_key, repo, ones_url),
    }


@tool
def pde_prototype(
    requirement: str,
    page_name: str = "",
    app_key: str = "",
    repo: str = "",
    ones_url: str = "",
    stage: str = "design",
    components: list[str] | None = None,
    output_dir: str = "",
) -> dict[str, Any]:
    """用点评 PDE Agent 产出页面原型图/设计稿。

    适用于出页面原型、设计稿、高保真 Demo。有 Picasso AppKey 或仓库时一并传入，
    便于进入真实代码环境；未配置 PDE 端点时本地生成可截图原型，并返回 DF/Multica 入口。
    操作指南：https://km.sankuai.com/collabpage/2776444575

    Args:
        requirement: 需求描述或 PRD 摘要。
        page_name: 页面名称（如「附近」「美食商户页」）。
        app_key: Picasso AppKey，可从 Picasso 门户复制。
        repo: Picasso 代码仓库地址。
        ones_url: 已有 ONES 需求链接；没有则可留空。
        stage: 阶段：requirements / ideation / design / code / device，
            也可用中文（需求分析、方案发散、原型图、设计稿、代码、真机）。
        components: 已知组件清单；为空则使用默认导航/主内容/底栏。
        output_dir: 原型 HTML 写入目录；默认 ``settings.artifacts_dir``。
    """
    if not requirement or not requirement.strip():
        raise ValueError("requirement must be a non-empty string")

    stage_key = _normalize_stage(stage)
    items = _clip_items(components)
    title = (page_name or requirement).strip()[:40]
    root = Path(output_dir).resolve() if (output_dir or "").strip() else Path(
        settings.artifacts_dir or "."
    ).resolve() / _safe_filename(title)

    try:
        remote = _try_remote(
            requirement.strip(),
            title,
            app_key.strip(),
            repo.strip(),
            ones_url.strip(),
            stage_key,
            items,
        )
    except Exception:
        logger.exception("pde_prototype: remote PDE endpoint failed, using local prototype")
        remote = None

    if remote:
        markup = _extract_remote_html(remote)
        html_path = ""
        if markup:
            html_path = str(_write_html(root, markup))
        elif remote.get("html_path"):
            html_path = str(remote.get("html_path"))
        remote_components = remote.get("components")
        return {
            "ok": True,
            "mode": "remote",
            "unverified": not bool(html_path),
            "stage": stage_key,
            "html_path": html_path,
            "task_url": str(remote.get("task_url") or remote.get("url") or ""),
            "components": remote_components if isinstance(remote_components, list) else items,
            "layout": str(remote.get("layout") or "remote PDE"),
            "interactions": remote.get("interactions") or ["确认方案"],
            "launch": _launch(),
            "playbook": str(remote.get("playbook") or _playbook(stage_key, app_key, repo, ones_url)),
        }

    return _local_result(
        requirement.strip(),
        title,
        stage_key,
        items,
        root,
        app_key.strip(),
        repo.strip(),
        ones_url.strip(),
    )
