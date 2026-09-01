"""截图视觉对比工具。"""

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from PIL import Image, ImageChops


class ScreenshotDiffError(RuntimeError):
    """当截图无法加载或无法对比时抛出。"""


@tool
def screenshot_diff(
    reference_path: str,
    actual_path: str,
    threshold: float = 0.95,
) -> dict[str, Any]:
    """对比两张截图并返回差异结果。

    使用 Pillow 做真实的像素级差异对比：将两张图统一缩放到相同尺寸后逐像素
    比较，相似度 = 1 - (不同像素数 / 总像素数)。

    Args:
        reference_path: 参考截图的路径。
        actual_path: 实际截图的路径。
        threshold: 相似度阈值（0-1），高于此值则对比通过。

    Returns:
        包含 ``pass``（bool）、``similarity``（float，0-1）以及
        ``diff_image_path``（str | None）键的字典。

    Raises:
        ValueError: 当参数无效时。
        ScreenshotDiffError: 当无法读取或对比图片时。
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")
    if not reference_path or not actual_path:
        raise ValueError("reference_path and actual_path must be non-empty")

    ref_file = Path(reference_path)
    actual_file = Path(actual_path)
    if not ref_file.exists() or not actual_file.exists():
        raise ScreenshotDiffError(
            f"reference or actual screenshot not found: {reference_path}, {actual_path}"
        )

    try:
        with Image.open(ref_file) as ref_img, Image.open(actual_file) as actual_img:
            ref_rgb = ref_img.convert("RGB")
            actual_rgb = actual_img.convert("RGB").resize(ref_rgb.size)

            diff = ImageChops.difference(ref_rgb, actual_rgb)
            diff_bbox = diff.getbbox()
            if diff_bbox is None:
                similarity = 1.0
            else:
                histogram = diff.histogram()
                # histogram 按 R/G/B 三个 256 桶拼接；index 0 桶代表差值为 0（完全相同）
                total_pixels = ref_rgb.size[0] * ref_rgb.size[1] * 3
                identical = histogram[0] + histogram[256] + histogram[512]
                similarity = identical / total_pixels if total_pixels else 0.0

            diff_image_path: str | None = None
            if similarity < threshold:
                diff_path = actual_file.with_name(f"{actual_file.stem}_diff.png")
                diff.save(diff_path)
                diff_image_path = str(diff_path)
    except OSError as exc:
        raise ScreenshotDiffError(f"failed to compare screenshots: {exc}") from exc

    return {
        "pass": similarity >= threshold,
        "similarity": round(similarity, 4),
        "diff_image_path": diff_image_path,
    }
