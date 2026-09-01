"""Demo 部署工具。"""

import json
import zipfile
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool

_VALID_ENVIRONMENTS = ("staging", "production")


class DemoDeployError(RuntimeError):
    """当 Demo 部署失败时抛出。"""


@tool
def deploy_demo(
    artifact_path: str,
    environment: Literal["staging", "production"] = "staging",
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将 Demo 产物打包写入本地磁盘（本地测试用途）。

    本地开发/测试场景下，直接在 ``artifact_path`` 处生成一个真实的 zip
    压缩包（内含 ``manifest.json`` 摘要，便于人工检查产物内容），而不是
    调用真实的远程部署流水线。

    Args:
        artifact_path: Demo 产物应写入的本地路径（含文件名，如
            ``.../build.zip``）。
        environment: 目标环境（``staging`` 或 ``production``）。
        manifest: 需要写入 ``manifest.json`` 的附加信息（如设计草稿、
            组件清单等），仅用于本地留痕，不影响部署状态。

    Returns:
        包含 ``status``（``"pending" | "success" | "failed"``）以及
        ``url`` 键的字典。

    Raises:
        ValueError: 当参数无效时。
        DemoDeployError: 当写入产物文件失败时。
    """
    if not artifact_path or not artifact_path.strip():
        raise ValueError("artifact_path must be a non-empty string")
    if environment not in _VALID_ENVIRONMENTS:
        raise ValueError(f"environment must be one of {_VALID_ENVIRONMENTS}")

    path = Path(artifact_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest or {}, ensure_ascii=False, indent=2),
            )
    except OSError as exc:
        raise DemoDeployError(f"failed to write demo artifact to {artifact_path}: {exc}") from exc

    return {"status": "success", "url": f"file://{path.resolve()}"}
