"""把 HTML 产物截成 PNG：优先 Playwright 真浏览器，否则按 DOM 光栅化。"""

from __future__ import annotations

import html as html_lib
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class BrowserScreenshotError(RuntimeError):
    """当 HTML 无法截图时抛出。"""


class _DomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.heading = ""
        self.items: list[str] = []
        self.bg = "#ffffff"
        self._capture_title = False
        self._capture_h1 = False
        self._capture_li = False
        self._buf = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        if tag == "title":
            self._capture_title = True
            self._buf = ""
        elif tag == "h1":
            self._capture_h1 = True
            self._buf = ""
        elif tag == "li":
            self._capture_li = True
            self._buf = ""
        elif tag in {"body", "html"}:
            style = attr.get("style", "")
            match = re.search(r"background(?:-color)?:\s*([^;]+)", style)
            if match:
                self.bg = match.group(1).strip()

    def handle_data(self, data: str) -> None:
        if self._capture_title or self._capture_h1 or self._capture_li:
            self._buf += data

    def handle_endtag(self, tag: str) -> None:
        text = " ".join(self._buf.split())
        if tag == "title" and self._capture_title:
            self.title = text
            self._capture_title = False
        elif tag == "h1" and self._capture_h1:
            self.heading = text
            self._capture_h1 = False
        elif tag == "li" and self._capture_li:
            if text:
                self.items.append(text)
            self._capture_li = False
        self._buf = ""


def _rasterize_html(html_text: str, output_path: Path) -> None:
    parser = _DomParser()
    parser.feed(html_text)
    bg = parser.bg if parser.bg.startswith("#") else "#ffffff"
    img = Image.new("RGB", (800, 600), color=bg)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    y = 24
    heading = parser.heading or parser.title or "Demo"
    draw.text((24, y), heading[:80], fill="#111111", font=font)
    y += 36
    for item in parser.items[:16]:
        draw.text((32, y), f"• {html_lib.unescape(item)[:88]}", fill="#222222", font=font)
        y += 28
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)


def _playwright_screenshot(html_path: Path, output_path: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output_path), full_page=False)
        return True
    except Exception:
        logger.exception("playwright screenshot failed")
        return False
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                logger.warning("playwright: browser.close() failed, process may leak")


@tool
def browser_screenshot(html_path: str, output_path: str) -> dict[str, Any]:
    """把本地 HTML 文件截成 PNG。优先用 Chromium；否则按 HTML DOM 光栅化。

    Args:
        html_path: HTML 文件路径。
        output_path: 输出 PNG 路径。
    """
    source = Path(html_path)
    target = Path(output_path)
    if not source.exists():
        raise BrowserScreenshotError(f"html not found: {html_path}")
    html_text = source.read_text(encoding="utf-8")
    if _playwright_screenshot(source, target):
        return {"ok": True, "mode": "browser", "output_path": str(target)}
    _rasterize_html(html_text, target)
    return {"ok": True, "mode": "html_raster", "output_path": str(target)}
