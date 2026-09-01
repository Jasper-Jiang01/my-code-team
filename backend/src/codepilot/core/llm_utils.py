"""LLM 输出解析工具。

大模型（如 LongCat-2.0）在返回 JSON 时经常会用 markdown 代码块包裹，
或在 JSON 前后添加额外说明文字。本模块提供统一的提取与解析函数，
避免各节点重复实现容错逻辑。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# 匹配 ```json ... ``` 或 ``` ... ``` 代码块
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
# 匹配第一个 { 到最后一个 } 之间的内容
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(raw: str) -> dict | list | None:
    """从 LLM 输出中提取并解析 JSON。

    按以下顺序尝试：
    1. 直接 json.loads（理想情况：纯 JSON 输出）
    2. 提取 ```json ... ``` 代码块后解析
    3. 提取第一个 { ... } 或 [ ... ] 片段后解析

    Args:
        raw: LLM 返回的原始文本。

    Returns:
        解析后的 dict / list，如果全部失败则返回 None。
    """
    if not raw or not raw.strip():
        return None

    raw = raw.strip()

    # 1. 直接解析
    try:
        parsed = json.loads(raw)
        return parsed
    except json.JSONDecodeError:
        pass

    # 2. 提取 markdown 代码块
    match = _CODE_BLOCK_RE.search(raw)
    if match:
        block = match.group(1).strip()
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass

    # 3. 提取第一个 { ... } 对象
    obj_match = _JSON_OBJECT_RE.search(raw)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass

    # 4. 提取第一个 [ ... ] 数组（逐字符匹配括号）
    start = raw.find("[")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        pass
                    break

    logger.warning("llm_utils.extract_json: failed to parse JSON from LLM output (first 200 chars): %s", raw[:200])
    return None


def safe_content(response) -> str:
    """从 LLM 响应中提取文本内容，处理非字符串类型的载荷。

    Args:
        response: LLM 返回的 AIMessage 或类似对象。

    Returns:
        纯文本字符串。
    """
    content = response.content if hasattr(response, "content") else response
    return content if isinstance(content, str) else str(content)
