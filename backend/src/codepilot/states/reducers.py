"""State Bus 使用的幂等 reducer。

``operator.add`` 只适合追加、不适合更新（例如把 issue 标为 resolved）。
把它用在父子图共享 schema 上还会在子图通道重放时重复累加。

这里的 reducer 都是幂等的：同一 ``id`` 再次出现会合并而不是复制，
因此主图可以把已编译子图直接挂为节点。
"""

from typing import Any


def unique_extend(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """追加 ``right`` 中尚未出现过的元素，保持原有顺序。"""
    result: list[Any] = list(left or [])
    for item in right or []:
        if item not in result:
            result.append(item)
    return result


def upsert_by_id(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """按 ``id`` 合并字典条目：相同 id 则右值覆盖，否则追加。

    没有 ``id`` 的条目退化为按完整相等去重后追加，避免丢失旧数据。
    """
    result: list[dict] = []
    index: dict[str, int] = {}

    def _absorb(item: Any) -> None:
        if not isinstance(item, dict):
            if item not in result:
                result.append(item)
            return
        item_id = item.get("id")
        if item_id:
            if item_id in index:
                result[index[item_id]] = {**result[index[item_id]], **item}
            else:
                index[item_id] = len(result)
                result.append(dict(item))
            return
        if item not in result:
            result.append(dict(item))

    for item in left or []:
        _absorb(item)
    for item in right or []:
        _absorb(item)
    return result
