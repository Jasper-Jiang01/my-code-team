"""数据分析工具集。

提供四个原子工具供 LLM Agent（如 ``sub_graph_data.py`` 数据分析子图）调用。

工具总览
========

1. ``analyze_data(file_path, question="")``
   加载数据文件 → EDA 摘要 → 可选的定向分析。
   - 作用：了解数据概况（行列数、每列 dtype / 缺失率、数值列统计摘要、
     类别列 Top-10 取值、日期列范围）+ 前 5 行预览。
   - ``question`` 非空时按关键词做定向分析：趋势（日期重采样走势）、
     分组对比（类别列 groupby 均值）、相关性（数值列相关矩阵）。
   - 典型场景：分析入口，拿到文件后第一个调用；后续再按需调用
     filter_rows / group_aggregate / plot_chart 深入。

2. ``filter_rows(file_path, column, op, value)``
   按单列条件过滤数据行，返回匹配行（Markdown 表格，最多 20 行）。
   - ``op`` 白名单：eq / ne / gt / ge / lt / le / in（value 为逗号分隔列表）。
   - 数值列自动尝试把 value 转为数字比较；列不存在 / 非法操作符
     返回可读错误而非异常。
   - 典型场景："看看北京的数据""销量大于 1000 的记录"。

3. ``group_aggregate(file_path, group_by, value_column, agg="sum", top_n=10)``
   按类别列分组对数值列聚合，返回降序 Top-N（Markdown 表格）。
   - ``agg`` 白名单：sum / mean / count / min / max / median。
   - ``top_n`` 会被夹紧到 [1, 50]；count 允许非数值列。
   - 典型场景："各城市销售额汇总""每个部门的平均薪资"。

4. ``plot_chart(file_path, x, y, kind="line", output_dir="")``
   生成图表并保存为 PNG，返回文件绝对路径（交给前端渲染）。
   - ``kind`` 白名单：line（折线）/ bar（柱状）/ scatter（散点）/
     hist（直方图，只需 y 一个数值列，x 可传空串）。
   - 超 1000 点自动均匀采样；bar 图超 10 个类别截断展示；
     自动探测系统中文字体（PingFang/雅黑/Noto 等）避免中文乱码。
   - 典型场景：“画个趋势图”“销售额分布直方图”。

5. ``ba_agent_analysis(question, file_path="", conversation_id="", timeout=600)``
   **兜底工具**：当上述四个本地原子工具都无法满足需求时，把问题
   转交给 BA-Agent 商业分析后端（baa-basic skill，见
   ``backend/skills/data_analyze``）。
   - 典型场景：需要外部业务数据/行业研究/竞对分析、经营诊断、
     指标异动归因、撰写完整分析报告/周报——这些超出了本地
     pandas 原子操作的能力范围。
   - 依赖美团内网：BA-Agent API（ba-ai.sankuai.com）需要 SSO
     鉴权；鉴权失败时返回引导信息而非自行替代分析。
   - 返回 markdown 分析结果 + 会话 ID + 会话链接；追问时把
     ``conversation_id`` 传回可复用上下文。
   - 本地工具能解决的一律用本地工具（更快、无网络依赖），
     本工具仅作能力补齐的最后手段。

安全设计
========
所有工具只做参数化的原子操作，绝不执行 LLM 下发的任意
代码（无 eval/exec，操作符与聚合函数均为白名单）；输出统一
序列化为 Markdown 并做长度截断（默认 4000 字符），防止撑爆
LLM 上下文。

数据加载的健壮性设计
====================
- 支持格式：.csv / .xlsx / .xls / .json / .jsonl（每行一个 JSON）；
- 编码容错：CSV 按 utf-8 → gbk → latin1 降级重试；
- 日期识别：str 列若大部分能解析为日期，自动转为 datetime，
  使 EDA / 趋势分析能命中日期列；
- 资源限制：拒绝超过 100MB 的文件。

异步设计
========
每个工具同时注册同步 ``func`` 与异步 ``coroutine``
（内部用 ``asyncio.to_thread`` 把 pandas 的同步计算丢进线程池），
因此 ``.invoke``（同步）与 ``.ainvoke``（异步）都可用——异步子图
调用时不会阻塞事件循环。

用法示例
========
::

    from codepilot.tools import analyze_data, filter_rows, group_aggregate, plot_chart

    report = analyze_data.invoke({"file_path": "sales.csv", "question": "哪个城市销售额最高"})
    rows = filter_rows.invoke({"file_path": "sales.csv", "column": "city", "op": "eq", "value": "北京"})
    top = group_aggregate.invoke({"file_path": "sales.csv", "group_by": "city",
                                  "value_column": "sales", "agg": "sum", "top_n": 5})
    png = plot_chart.invoke({"file_path": "sales.csv", "x": "date", "y": "sales", "kind": "line"})
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量：限制资源占用与输出长度
# ---------------------------------------------------------------------------
_MAX_FILE_BYTES = 100 * 1024 * 1024  # 拒绝超过 100MB 的文件
_MAX_PROFILE_COLUMNS = 30  # EDA 摘要最多展示的列数
_MAX_CATEGORY_ITEMS = 10  # 类别列 Top-N 展示条数
_MAX_OUTPUT_CHARS = 4000  # 工具输出硬截断
_MAX_PREVIEW_ROWS = 5  # 数据预览行数
_MAX_FILTER_ROWS = 20  # filter_rows 结果展示行数
_MAX_CHART_POINTS = 1000  # plot_chart 最多绘制的数据点数
_DATETIME_SAMPLE_ROWS = 100  # 日期探测的采样行数
_DATETIME_HIT_RATIO = 0.8  # 采样中可解析比例达到该阈值才视为日期列

# CSV 读取的编码降级顺序：utf-8 → gbk（中文 Windows 导出常见）→ latin1（兜底）
_CSV_ENCODINGS = ("utf-8", "gbk", "latin1")

# 增强-JSONL：每行一个 JSON 对象的常见格式
_SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xls", ".json", ".jsonl"}
_AGG_FUNCS = {"sum", "mean", "count", "min", "max", "median"}
_CHART_KINDS = {"line", "bar", "scatter", "hist"}


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """对工具输出做硬截断，防止撑爆 LLM 上下文。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n...(输出过长，已截断)"


def _df_to_markdown(df: pd.DataFrame) -> str:
    """DataFrame → Markdown 表格；空表与格式异常时降级为字符串。"""
    if df is None or df.empty:
        return "（无数据）"
    try:
        return df.to_markdown(index=False)
    except Exception:  # noqa: BLE001 —— to_markdown 失败时降级
        return df.to_string(index=False)


# ---------------------------------------------------------------------------
# 第 2 步：数据加载与校验（含增强项：编码容错 / JSONL / 日期识别）
# ---------------------------------------------------------------------------
def _read_csv_with_fallback(path: Path) -> pd.DataFrame:
    """增强-编码容错：按 utf-8 → gbk → latin1 顺序尝试读取 CSV。

    中文 Windows 环境导出的 CSV 常为 gbk 编码，直接 read_csv 会抛
    UnicodeDecodeError；latin1 理论上能解码任意字节流，作为最终兜底。
    三种编码都失败时抛出最后一次的异常，由上层统一捕获回传给 LLM。
    """
    last_error: Exception | None = None
    for encoding in _CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue  # 编码不匹配，降级到下一个编码重试
    assert last_error is not None  # latin1 不会抛 UnicodeDecodeError，仅为类型收敛
    raise last_error


def _coerce_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """增强-日期识别：把"大部分值像日期"的 str 列转成 datetime。

    CSV 中的日期列读入后是 str dtype，``_profile`` / ``_directed_analysis``
    都不会把它当日期列处理（趋势分析因此失效）。这里对每个类别型列
    采样 ``_DATETIME_SAMPLE_ROWS`` 行做 ``pd.to_datetime(errors="coerce")``
    探测：可解析比例 >= ``_DATETIME_HIT_RATIO`` 才整列转换。

    注意事项：
    - 数值列 / 布尔列不探测（避免把年份、ID 误判成日期）；
    - 解析失败（格式混杂等）直接跳过该列，不影响其他列；
    - pandas 3.0 对混合格式会抛异常而非告警，因此整体 try/except。
    """
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_datetime64_any_dtype(series):
            continue  # 已是日期列
        if not _is_category_like(series) or pd.api.types.is_bool_dtype(series):
            continue  # 只探测字符串/类别列

        sample = series.dropna().head(_DATETIME_SAMPLE_ROWS)
        if sample.empty:
            continue
        try:
            # 探测阶段未指定 format 时 pandas 会发 UserWarning（逐元素回退
            # dateutil 解析），这属于预期的探测行为，静默掉避免污染日志
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                converted = pd.to_datetime(sample, errors="coerce")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping non-date column %s: %s", col, exc)
            continue

        if float(converted.notna().mean()) >= _DATETIME_HIT_RATIO:
            df[col] = pd.to_datetime(series, errors="coerce")
    return df


def _load_dataframe(file_path: str) -> tuple[pd.DataFrame | None, str]:
    """按后缀加载文件，返回 (df, 错误信息)。成功时错误信息为空串。

    加载完成后会调用 ``_coerce_datetime_columns`` 做日期列探测转换，
    使下游 EDA / 趋势分析无需感知"日期列原来是字符串"这一细节。
    """
    path = Path(file_path).expanduser()

    if not path.exists():
        return None, f"文件不存在: {file_path}"
    if not path.is_file():
        return None, f"路径不是文件: {file_path}"

    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        return (
            None,
            f"不支持的文件格式: {suffix or '(无后缀)'}，仅支持 {sorted(_SUPPORTED_SUFFIXES)}",
        )

    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        return None, f"文件过大（{size / 1024 / 1024:.1f}MB），超过 100MB 限制"

    try:
        if suffix == ".csv":
            df = _read_csv_with_fallback(path)  # 增强-编码容错
        elif suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        elif suffix == ".jsonl":  # 增强-JSONL：每行一个 JSON 对象
            df = pd.read_json(path, lines=True)
        else:  # .json
            df = pd.read_json(path)
    except Exception as exc:  # noqa: BLE001 —— 错误信息要回传给 LLM
        return None, f"加载数据失败: {exc}"

    if df.empty:
        return None, "数据文件为空，没有可分析的行。"
    return _coerce_datetime_columns(df), ""


# ---------------------------------------------------------------------------
# 第 3 步：探索性统计（EDA）
# ---------------------------------------------------------------------------
def _is_category_like(series: pd.Series) -> bool:
    """判断是否为类别型列（object / str / category）。

    pandas 3.0 起字符串列默认使用 ``str`` dtype 而非 ``object``，
    因此需要同时兼容两种判断；category 用 CategoricalDtype 判断
    以避开已废弃的 ``is_categorical_dtype``。
    """
    if isinstance(series.dtype, pd.CategoricalDtype):
        return True
    return bool(pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series))


def _profile(df: pd.DataFrame) -> dict[str, Any]:
    """生成 EDA 摘要：形状、列信息、数值列摘要、类别列 Top-N。"""
    truncated_cols = len(df.columns) > _MAX_PROFILE_COLUMNS
    columns = df.columns[:_MAX_PROFILE_COLUMNS]

    column_info: dict[str, Any] = {}
    numeric_cols: list[str] = []
    category_cols: list[str] = []
    datetime_cols: list[str] = []

    for col in columns:
        series = df[col]
        dtype = str(series.dtype)
        missing_rate = round(float(series.isna().mean()) * 100, 2)
        info: dict[str, Any] = {"dtype": dtype, "缺失率%": missing_rate}

        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            numeric_cols.append(str(col))
            desc = series.describe().round(2)
            info["摘要"] = {k: float(v) for k, v in desc.items()}
        elif pd.api.types.is_datetime64_any_dtype(series):
            datetime_cols.append(str(col))
            info["范围"] = f"{series.min()} ~ {series.max()}"
        elif _is_category_like(series):
            category_cols.append(str(col))
            top = series.value_counts().head(_MAX_CATEGORY_ITEMS)
            info["Top取值"] = {str(k): int(v) for k, v in top.items()}
            info["唯一值数"] = int(series.nunique())

        column_info[str(col)] = info

    return {
        "行数": len(df),
        "列数": len(df.columns),
        "列数已截断": truncated_cols,
        "数值列": numeric_cols,
        "类别列": category_cols,
        "日期列": datetime_cols,
        "列详情": column_info,
    }


# ---------------------------------------------------------------------------
# 第 4 步：结合 question 的定向分析（关键词启发式）
# ---------------------------------------------------------------------------
_QUESTION_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("趋势", "trend", re.compile(r"趋势|增长|下降|变化|环比|同比|走势")),
    ("分组对比", "groupby", re.compile(r"对比|分组|分类|哪个.*(?:最|更)|不同")),
    ("相关性", "corr", re.compile(r"相关|影响|关系|关联|因素")),
]


def _directed_analysis(df: pd.DataFrame, question: str) -> str:
    """按问题关键词做定向分析，返回 Markdown 片段（可能为空串）。

    得益于 ``_load_dataframe`` 里的日期列探测（增强-日期识别），
    CSV 中原本为 str 的日期列到达这里时已是 datetime dtype，
    "趋势"类问题可以直接命中按日期重采样的分支。
    """
    sections: list[str] = []

    matched = {name for name, _, pattern in _QUESTION_RULES if pattern.search(question)}
    numeric_cols = [
        c
        for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
    ]
    datetime_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    category_cols = [c for c in df.columns if _is_category_like(df[c])]

    if ("趋势" in matched or "分组对比" in matched) and datetime_cols:
        # 按第一个日期列重采样，观察数值列的整体走势
        dt_col = datetime_cols[0]
        try:
            indexed = df.set_index(dt_col)[numeric_cols] if numeric_cols else None
            if indexed is not None:
                resampled = indexed.resample("D").mean().round(2)
                if len(resampled) > _MAX_CATEGORY_ITEMS:
                    resampled = pd.concat([resampled.head(5), resampled.tail(5)])
                sections.append(
                    f"### 按日期（{dt_col}）的数值走势\n\n{_df_to_markdown(resampled.reset_index())}"
                )
        except Exception as exc:  # noqa: BLE001
            sections.append(f"### 按日期走势\n\n（日期重采样失败: {exc}）")

    if "分组对比" in matched and category_cols and numeric_cols:
        # 按第一个类别列分组，对数值列做均值聚合
        cat_col, num_col = category_cols[0], numeric_cols[0]
        try:
            grouped = (
                df.groupby(cat_col, dropna=True)[num_col]
                .mean()
                .round(2)
                .sort_values(ascending=False)
                .head(_MAX_CATEGORY_ITEMS)
            )
            sections.append(
                f"### 按 {cat_col} 分组的 {num_col} 均值（Top {_MAX_CATEGORY_ITEMS}）\n\n"
                + _df_to_markdown(grouped.reset_index())
            )
        except Exception as exc:  # noqa: BLE001
            sections.append(f"### 分组对比\n\n（分组聚合失败: {exc}）")

    if "相关性" in matched and len(numeric_cols) >= 2:
        try:
            corr = df[numeric_cols].corr(numeric_only=True).round(2)
            sections.append(f"### 数值列相关性矩阵\n\n{_df_to_markdown(corr.reset_index())}")
        except Exception as exc:  # noqa: BLE001
            sections.append(f"### 相关性\n\n（相关性计算失败: {exc}）")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# 增强-异步：同步实现 + 线程池包装
# ---------------------------------------------------------------------------
# 设计说明：
# - pandas 是同步库，直接在 async def 里调用会阻塞事件循环；
# - 因此每个工具拆成「同步实现 _xxx_impl + 异步包装 _xxx_async」两层，
#   异步包装用 asyncio.to_thread 把同步计算丢进线程池；
# - 最后用 StructuredTool.from_function(func=..., coroutine=...) 注册，
#   同时获得 .invoke（同步）与 .ainvoke（异步）两条执行路径，
#   LLM Agent 无论同步还是异步运行都能安全调用。
# ---------------------------------------------------------------------------


def _analyze_data_impl(file_path: str, question: str = "") -> str:
    """分析 CSV/Excel/JSON/JSONL 数据文件，返回统计摘要（EDA）与定向分析结果。

    适用场景：用户给出数据文件并希望了解数据概况、分布、缺失情况，
    或提出具体分析问题（如"哪个城市销售额最高""销售额趋势如何"）。
    日期样式的字符串列会被自动识别为日期列，支持趋势类分析。

    Args:
        file_path: 数据文件的路径，支持 .csv / .xlsx / .xls / .json / .jsonl。
        question: 可选的分析问题。提供时会按问题关键词做定向分析
            （趋势 / 分组对比 / 相关性）；为空时仅返回 EDA 摘要。

    Returns:
        Markdown 格式的分析报告；加载失败时返回以"文件不存在"、
        "不支持的文件格式"或"加载数据失败"开头的错误说明。
    """
    df, error = _load_dataframe(file_path)
    if df is None:
        return error

    try:
        profile = _profile(df)
    except Exception as exc:  # noqa: BLE001
        return f"生成数据概况失败: {exc}"

    parts = [
        "## 数据概况",
        "```json\n" + json.dumps(profile, ensure_ascii=False, default=str) + "\n```",
        f"## 前 {_MAX_PREVIEW_ROWS} 行预览",
        _df_to_markdown(df.head(_MAX_PREVIEW_ROWS)),
    ]

    if question:
        parts.append(f"## 针对「{question}」的定向分析")
        directed = _directed_analysis(df, question)
        parts.append(directed or "（未能按问题匹配到可自动执行的分析，请基于上方摘要继续分析）")

    return _truncate("\n\n".join(parts))


async def _analyze_data_async(file_path: str, question: str = "") -> str:
    """``_analyze_data_impl`` 的异步包装：在线程池中执行同步 pandas 逻辑。"""
    return await asyncio.to_thread(_analyze_data_impl, file_path, question)


def _filter_rows_impl(file_path: str, column: str, op: str, value: str) -> str:
    """按单列条件过滤数据行，返回匹配的行（Markdown 表格）。

    用于替代执行任意 pandas 代码：只支持有限的比较操作，安全可控。

    Args:
        file_path: 数据文件路径（.csv/.xlsx/.xls/.json/.jsonl）。
        column: 要过滤的列名，必须存在于数据中。
        op: 比较操作符，可选 eq(等于) / ne(不等于) / gt(大于) /
            ge(大于等于) / lt(小于) / le(小于等于) / in(属于逗号分隔列表)。
        value: 比较值；op 为 in 时为逗号分隔的多个值；数值列会自动尝试
            转为数字比较。

    Returns:
        Markdown 格式的匹配结果（最多 20 行），或错误说明。
    """
    df, error = _load_dataframe(file_path)
    if df is None:
        return error

    if column not in df.columns:
        return f"列不存在: {column}。可用列: {list(df.columns)}"

    ops = {
        "eq": lambda s, v: s == v,
        "ne": lambda s, v: s != v,
        "gt": lambda s, v: s > v,
        "ge": lambda s, v: s >= v,
        "lt": lambda s, v: s < v,
        "le": lambda s, v: s <= v,
    }

    series = df[column]
    try:
        if op == "in":
            values = [v.strip() for v in value.split(",") if v.strip()]
            if pd.api.types.is_numeric_dtype(series):
                converted = pd.to_numeric(pd.Series(values), errors="coerce")
                mask = series.isin(converted.dropna().tolist())
            else:
                mask = series.isin(values)
        elif op in ops:
            if pd.api.types.is_numeric_dtype(series):
                cmp_value: Any = float(value)
            else:
                cmp_value = value
            mask = ops[op](series, cmp_value)
        else:
            return f"不支持的操作符: {op}。可选: {sorted([*ops, 'in'])}"
    except ValueError:
        return f"数值列 {column} 无法将 {value!r} 解析为数字"
    except Exception as exc:  # noqa: BLE001
        return f"过滤失败: {exc}"

    result = df[mask]
    if result.empty:
        return "没有匹配的行。"

    total = len(result)
    shown = result.head(_MAX_FILTER_ROWS)
    note = (
        ""
        if total <= _MAX_FILTER_ROWS
        else f"\n\n（共匹配 {total} 行，仅展示前 {_MAX_FILTER_ROWS} 行）"
    )
    return _truncate(f"匹配 {total} 行：\n\n{_df_to_markdown(shown)}{note}")


async def _filter_rows_async(file_path: str, column: str, op: str, value: str) -> str:
    """``_filter_rows_impl`` 的异步包装：在线程池中执行同步 pandas 逻辑。"""
    return await asyncio.to_thread(_filter_rows_impl, file_path, column, op, value)


def _group_aggregate_impl(
    file_path: str,
    group_by: str,
    value_column: str,
    agg: str = "sum",
    top_n: int = 10,
) -> str:
    """按类别列分组对数值列聚合，返回排序后的结果（Markdown 表格）。

    用于替代执行任意 pandas 代码：聚合函数白名单受限，安全可控。

    Args:
        file_path: 数据文件路径（.csv/.xlsx/.xls/.json/.jsonl）。
        group_by: 分组列名（类别列），必须存在于数据中。
        value_column: 聚合的数值列名，必须存在于数据中。
        agg: 聚合函数，可选 sum / mean / count / min / max / median。
        top_n: 返回前 N 个分组，默认 10，最大 50。

    Returns:
        Markdown 格式的聚合结果，或错误说明。
    """
    df, error = _load_dataframe(file_path)
    if df is None:
        return error

    if agg not in _AGG_FUNCS:
        return f"不支持的聚合函数: {agg}。可选: {sorted(_AGG_FUNCS)}"
    for col in (group_by, value_column):
        if col not in df.columns:
            return f"列不存在: {col}。可用列: {list(df.columns)}"
    if agg != "count" and not pd.api.types.is_numeric_dtype(df[value_column]):
        return f"列 {value_column} 不是数值列，无法执行 {agg}"

    top_n = max(1, min(int(top_n), 50))

    try:
        grouped = (
            df.groupby(group_by, dropna=True)[value_column]
            .agg(agg)
            .round(2)
            .sort_values(ascending=False)
            .head(top_n)
            .reset_index()
        )
    except Exception as exc:  # noqa: BLE001
        return f"聚合失败: {exc}"

    if grouped.empty:
        return "分组结果为空。"
    return _truncate(
        f"按 {group_by} 对 {value_column} 执行 {agg}（Top {top_n}）：\n\n{_df_to_markdown(grouped)}"
    )


async def _group_aggregate_async(
    file_path: str,
    group_by: str,
    value_column: str,
    agg: str = "sum",
    top_n: int = 10,
) -> str:
    """``_group_aggregate_impl`` 的异步包装：在线程池中执行同步 pandas 逻辑。"""
    return await asyncio.to_thread(
        _group_aggregate_impl, file_path, group_by, value_column, agg, top_n
    )


# ---------------------------------------------------------------------------
# 增强-图表工具：plot_chart（输出 PNG 路径，前端再渲染）
# ---------------------------------------------------------------------------
# 候选中文字体（按优先级）：macOS / Windows / Linux 常见 CJK 字体。
# matplotlib 默认的 DejaVu Sans 不含中文字形，中文标签会渲染成方框（□）。
_CJK_FONTS = (
    "PingFang SC",  # macOS
    "Hiragino Sans GB",  # macOS 备选
    "Arial Unicode MS",  # macOS 老版本
    "Microsoft YaHei",  # Windows
    "SimHei",  # Windows 老版本
    "Noto Sans CJK SC",  # Linux（noto-fonts-cjk）
)


def _select_cjk_font(plt: Any) -> str | None:
    """选择第一个可用的中文字体并写入 rcParams，返回字体名。

    找不到任何 CJK 字体时返回 None（此时保持默认字体并静默字形告警，
    图表仍能生成，只是中文标签会显示为方框）。
    """
    from matplotlib import font_manager

    for name in _CJK_FONTS:
        try:
            font_manager.findfont(name, fallback_to_default=False)
        except Exception as exc:  # noqa: BLE001
            logger.debug("CJK font %s is unavailable: %s", name, exc)
            continue
        # 命中：设为首选 sans 字体，DejaVu Sans 兜底保证英文/数字正常
        plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False  # 中文字体缺负号字形时的常见坑
        return name
    return None


def _plot_chart_impl(
    file_path: str,
    x: str,
    y: str,
    kind: str = "line",
    output_dir: str = "",
) -> str:
    """为数据文件生成图表，返回保存的 PNG 绝对路径。

    适用场景：需要可视化呈现数据（趋势线、对比柱状图、散点分布、
    直方图）时调用；返回的 PNG 路径可交给前端渲染。

    Args:
        file_path: 数据文件路径（.csv/.xlsx/.xls/.json/.jsonl）。
        x: X 轴列名。kind 为 hist 时可传空串（此时必须提供 y）。
        y: Y 轴列名。kind 为 hist 时为目标数值列；其余 kind 必填。
        kind: 图表类型，可选 line(折线) / bar(柱状) / scatter(散点) /
            hist(直方图)。
        output_dir: PNG 输出目录，默认保存到数据文件所在目录。

    Returns:
        成功时返回 PNG 绝对路径；失败时返回以"图表生成失败"等开头的
        错误说明。
    """
    df, error = _load_dataframe(file_path)
    if df is None:
        return error

    if kind not in _CHART_KINDS:
        return f"不支持的图表类型: {kind}。可选: {sorted(_CHART_KINDS)}"

    # hist 只需要一列数值数据（x、y 至少提供一个有效的数值列）
    hist_col = y or x
    if kind == "hist":
        if not hist_col:
            return "kind 为 hist 时必须提供 x 或 y 中的数值列"
        if hist_col not in df.columns:
            return f"列不存在: {hist_col}。可用列: {list(df.columns)}"
        if not pd.api.types.is_numeric_dtype(df[hist_col]):
            return f"列 {hist_col} 不是数值列，无法绘制直方图"
    else:
        for col in (x, y):
            if not col:
                return f"kind 为 {kind} 时 x 和 y 均不能为空"
            if col not in df.columns:
                return f"列不存在: {col}。可用列: {list(df.columns)}"

    # matplotlib 懒加载：仅在真正画图时导入（且用 Agg 后端，
    # 避免在无显示环境的服务器上报错）。未安装时给出可操作的提示
    # 而不是让模块 import 失败。
    try:
        import matplotlib

        matplotlib.use("Agg")  # 非交互后端：只写文件，不弹窗
        import matplotlib.pyplot as plt
    except ImportError:
        return "图表生成失败: 未安装 matplotlib，请先执行 `uv pip install matplotlib`"

    # 中文字体：命中则中文标签正常渲染；未命中时仍可出图（英文/数字正常）
    cjk_font = _select_cjk_font(plt)

    try:
        # 数据点限制：超出 _MAX_CHART_POINTS 时均匀采样（保留首尾、
        # 中间等间隔抽取），既控制渲染开销又保住趋势形状
        note = ""
        if len(df) > _MAX_CHART_POINTS:
            step = len(df) // _MAX_CHART_POINTS
            df = df.iloc[::step]
            note = f"（原始数据 {len(df) * step} 行，已均匀采样至 {len(df)} 点绘制）"

        fig, ax = plt.subplots(figsize=(10, 6))
        if kind == "hist":
            ax.hist(df[hist_col].dropna(), bins=30, edgecolor="white")
            ax.set_xlabel(hist_col)
            ax.set_ylabel("频数")
        elif kind == "line":
            ax.plot(df[x], df[y], marker="o", markersize=3, linewidth=1.5)
            ax.set_xlabel(x)
            ax.set_ylabel(y)
        elif kind == "bar":
            # 类别过多时只保留前 _MAX_CATEGORY_ITEMS 个，避免柱子挤成一团
            if len(df) > _MAX_CATEGORY_ITEMS:
                df = df.head(_MAX_CATEGORY_ITEMS)
                note = note or f"（柱状图仅展示前 {_MAX_CATEGORY_ITEMS} 个类别）"
            ax.bar(df[x].astype(str), df[y])
            ax.set_xlabel(x)
            ax.set_ylabel(y)
            plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
        else:  # scatter
            ax.scatter(df[x], df[y], s=12, alpha=0.6)
            ax.set_xlabel(x)
            ax.set_ylabel(y)

        ax.set_title(f"{hist_col if kind == 'hist' else f'{y} vs {x}'} ({kind})")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        # 输出路径：默认保存到数据文件同目录，文件名带时间戳避免覆盖
        out_dir = (
            Path(output_dir).expanduser() if output_dir else Path(file_path).expanduser().parent
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        png_path = out_dir / f"{Path(file_path).stem}_chart_{kind}_{stamp}.png"
        # 无中文字体时 savefig 会逐字形发 UserWarning（Glyph missing），
        # 图仍能生成（中文显示为方框），静默掉并在返回值里提示
        with warnings.catch_warnings():
            if not cjk_font:
                warnings.simplefilter("ignore", UserWarning)
            fig.savefig(png_path, dpi=120)
        plt.close(fig)  # 及时释放，防止长会话下 figure 累积泄漏
    except Exception as exc:  # noqa: BLE001
        return f"图表生成失败: {exc}"

    font_note = "" if cjk_font else "（系统缺少中文字体，中文标签可能显示为方框）"
    return f"图表已保存: {png_path}{note}{font_note}"


async def _plot_chart_async(
    file_path: str,
    x: str,
    y: str,
    kind: str = "line",
    output_dir: str = "",
) -> str:
    """``_plot_chart_impl`` 的异步包装：画图是 CPU/IO 混合操作，放线程池执行。"""
    return await asyncio.to_thread(_plot_chart_impl, file_path, x, y, kind, output_dir)


# ---------------------------------------------------------------------------
# 兜底工具：BA-Agent 商业分析助手（baa-basic skill）
# ---------------------------------------------------------------------------
# 定位：analyze_data / filter_rows / group_aggregate / plot_chart 四个本地
# 原子工具只能处理「已拿到数据文件」的 pandas 操作；当用户需要外部业务
# 数据、行业研究、经营诊断、指标异动归因、完整分析报告等能力时，把问题
# 转交给 BA-Agent 后端（backend/skills/data_analyze，来自 FRIDAY Skillhub
# 的 baa-basic 官方 skill）。
#
# 集成方式：以子进程方式调用 skill 自带的 call_ba_agent.py CLI。遵循其
# references/auth.md 的「子 agent 内禁止重鉴权」约定：统一传 --no-reauth，
# 鉴权失败时把可操作的引导信息返回给 LLM/用户，而不是在工具内部触发
# 登录流程（MOA/CatDesk/Playwright 登录需要交互式环境，服务端无法完成）。

# 允许通过环境变量覆盖 skill 目录（默认：backend/skills/data_analyze）
_BAA_SKILL_DIR_ENV = "CODEPILOT_BAA_SKILL_DIR"
# BA-Agent 流式分析可能耗时较长（skill 官方子 agent 的超时是 1800s），
# 这里给工具一个可由 LLM 覆盖的默认超时
_BAA_DEFAULT_TIMEOUT_SECONDS = 600
# BA-Agent 返回的是完整分析报告，比本地工具的原子输出允许更长
_BAA_MAX_OUTPUT_CHARS = 12000


def _ba_agent_script_path() -> Path:
    """定位 call_ba_agent.py 脚本。

    查找顺序：环境变量 ``CODEPILOT_BAA_SKILL_DIR`` → 项目内默认路径
    （``<backend>/skills/data_analyze``）。返回路径但不校验存在性，
    由调用方给出可读错误。
    """
    override = os.environ.get(_BAA_SKILL_DIR_ENV)
    if override:
        base = Path(override).expanduser()
    else:
        # 本文件位于 backend/src/codepilot/tools/，同上 3 级即 backend/
        base = Path(__file__).resolve().parents[3] / "skills" / "data_analyze"
    return base / "scripts" / "call_ba_agent.py"


def _format_baa_error(exit_code: int, message: str) -> str:
    """把 call_ba_agent.py 的失败输出转成给 LLM 的可操作错误信息。

    鉴权类失败（退出码 2/3 或错误文案命中关键词）追加引导：如何
    重新鉴权、或前往 BA-Agent 平台手动操作（skill 规定严禁在鉴权
    失败后改用其他工具自行替代分析）。
    """
    text = _truncate(message, 2000)
    auth_keywords = re.compile(r"鉴权|token|未登录|401|403", re.IGNORECASE)
    if exit_code in (2, 3) or auth_keywords.search(text):
        return (
            f"{text}\n\n"
            "【鉴权未通过】BA-Agent 需要美团内网 SSO 登录态。请先在具备"
            "登录态的环境执行 `python "
            f"{_ba_agent_script_path().parent}/inject-ba-cookie-moa.py` 完成鉴权，"
            "或引导用户前往 https://ba-ai.sankuai.com 手动分析。"
            "严禁绕过 BA-Agent 用其他方式自行替代分析。"
        )
    return text


def _run_ba_agent(args: list[str], timeout: int) -> tuple[int, str]:
    """同步执行 call_ba_agent.py 子命令，返回 (退出码, 输出文本)。

    统一使用当前解释器（sys.executable）运行，避免宿主机无 ``python3``
    命令名的环境差异。超时/脚本缺失时返回非 0 退出码与可读错误。
    """
    script = _ba_agent_script_path()
    if not script.is_file():
        return (
            127,
            (
                f"未找到 BA-Agent 脚本: {script}。请确认 baa-basic skill 已安装到 "
                f"backend/skills/data_analyze（或通过 {_BAA_SKILL_DIR_ENV} 环境变量指定目录）。"
            ),
        )
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, (
            f"BA-Agent 调用超过 {timeout}s 未返回。分析可能仍在后台运行，"
            "可稍后重试（追问时传入同一 conversation_id 可能拿到结果）。"
        )
    except OSError as exc:
        return 127, f"BA-Agent 脚本启动失败: {exc}"

    output = (proc.stdout or "").strip()
    if proc.returncode != 0:
        error_text = (proc.stderr or "").strip() or output
        return proc.returncode, _format_baa_error(proc.returncode, error_text)
    return 0, output


def _ba_agent_analysis_impl(
    question: str,
    file_path: str = "",
    conversation_id: str = "",
    timeout: int = _BAA_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """兜底：把本地工具无法解决的分析问题转交给 BA-Agent 商业分析后端。

    仅在 analyze_data / filter_rows / group_aggregate / plot_chart 四个
    本地工具都无法满足时调用，典型场景：需要外部业务数据或行业研究、
    竞对分析、经营诊断、指标异动归因、撰写完整分析报告/周报。
    本地数据文件的基本探索、过滤、聚合、画图一律优先用本地工具。

    Args:
        question: 用户的分析问题（完整、具体，包含必要背景）。
        file_path: 可选的本地数据文件路径，会随问题一起上传给 BA-Agent。
        conversation_id: 可选的会话 ID。对同一话题追问时传入上一次返回
            的会话 ID 可复用上下文；新话题留空。
        timeout: 子进程超时秒数，默认 600。

    Returns:
        成功时返回 markdown 分析结果（含会话 ID / 会话链接，供追问复用）；
        失败时返回以错误说明开头、附带可操作引导的文本。
    """
    if not question.strip():
        return "请提供分析问题。"

    # 按子 agent 约定传 --no-reauth：鉴权失败直接报错返回，
    # 由调用方（LLM/用户）决定后续，不在工具内触发交互式重登录
    args: list[str] = ["analyze", question, "--no-reauth"]
    if file_path:
        # skill 要求带文件时必须传 --display-name 为原始文件名
        args += ["--file", file_path, "--display-name", Path(file_path).name]
    if conversation_id:
        args += ["--conversation-id", conversation_id]

    code, output = _run_ba_agent(args, timeout)
    if code != 0:
        return f"BA-Agent 分析失败: {output}"

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        # 非 JSON（脚本异常输出等）：原样截断返回，交由 LLM 判断
        return _truncate(output or "（BA-Agent 未返回内容）", _BAA_MAX_OUTPUT_CHARS)

    # 流式断开（ended=false）：自动 reconnect 续传一次，仍断开则提示
    if payload.get("ended") is False:
        rc_args = ["reconnect", str(payload.get("conversationId", ""))]
        if payload.get("chatResponseId"):
            rc_args.append(str(payload["chatResponseId"]))
        rc_args.append("--no-reauth")
        rc_code, rc_output = _run_ba_agent(rc_args, timeout)
        if rc_code == 0:
            try:
                payload = json.loads(rc_output)
            except json.JSONDecodeError:
                pass  # 保持原 payload

    parts: list[str] = []
    conv = payload.get("conversationId") or conversation_id
    if conv:
        parts.append(f"会话 ID: {conv}（追问时传入 conversation_id 可复用上下文）")
    if payload.get("chatUrl"):
        parts.append(f"会话链接: {payload['chatUrl']}")
    markdown = (payload.get("markdown") or "").strip()
    if markdown:
        parts.append(markdown)
    if payload.get("ended") is False:
        parts.append("（提示：本轮流式返回未完整结束，结论可能被截断，可再次调用追问）")
    if not parts:
        return (
            "BA-Agent 未返回分析结果，请稍后重试，"
            "或引导用户前往 https://ba-ai.sankuai.com 手动分析。"
        )
    return _truncate("\n\n".join(parts), _BAA_MAX_OUTPUT_CHARS)


async def _ba_agent_analysis_async(
    question: str,
    file_path: str = "",
    conversation_id: str = "",
    timeout: int = _BAA_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """``_ba_agent_analysis_impl`` 的异步包装：子进程等待是阻塞 IO，放线程池执行。"""
    return await asyncio.to_thread(
        _ba_agent_analysis_impl, question, file_path, conversation_id, timeout
    )


# ---------------------------------------------------------------------------
# 第 1 步：工具注册
# ---------------------------------------------------------------------------
# 说明：这里统一用 StructuredTool.from_function 而非 @tool 装饰器，
# 因为需要同时注册 func（同步路径）与 coroutine（异步路径）。
# description / args_schema 会从同步实现的签名与 docstring 自动推导。
analyze_data = StructuredTool.from_function(
    func=_analyze_data_impl,
    coroutine=_analyze_data_async,
    name="analyze_data",
)
filter_rows = StructuredTool.from_function(
    func=_filter_rows_impl,
    coroutine=_filter_rows_async,
    name="filter_rows",
)
group_aggregate = StructuredTool.from_function(
    func=_group_aggregate_impl,
    coroutine=_group_aggregate_async,
    name="group_aggregate",
)
plot_chart = StructuredTool.from_function(
    func=_plot_chart_impl,
    coroutine=_plot_chart_async,
    name="plot_chart",
)
ba_agent_analysis = StructuredTool.from_function(
    func=_ba_agent_analysis_impl,
    coroutine=_ba_agent_analysis_async,
    name="ba_agent_analysis",
)


# ---------------------------------------------------------------------------
# TODO：后续待办（第 7~8 步），完成一项删一项
# ---------------------------------------------------------------------------

# TODO(第7步-挂载子图): 在 sub_graph_data.py 中把工具挂载到数据分析 Agent：
#   from codepilot.model import create_model
#   from codepilot.tools import analyze_data, filter_rows, group_aggregate, plot_chart
#   model = create_model()  # 复用 LRU 缓存的模型实例
#   # 方案 A：create_react_agent(model, tools=[...]) 后 add_node 挂载；
#   # 方案 B：走 deepagents（参考 main_workflow.py 的 build_deep_agent）。
#   # 注意：子图直接 add_node 挂载（共享 messages 通道），不要在节点函数里
#   # 手动 invoke，否则 interrupt 无法冒泡到主图。
#   # 异步提示：四个工具均支持 .ainvoke（内部 asyncio.to_thread），
#   # 异步子图直接 await 调用即可，不会阻塞事件循环。

# TODO(第7步-统一注册): tools/__init__.py 目前导出为 __tools__；若后续子图/
#   主工作流按 __all__ 约定批量收集工具（如 for t in tools.__all__），
#   需同步调整命名或补一个收集函数（如 get_data_tools() -> list[BaseTool]）。

# TODO(第8步-单元测试): 在 backend/tests/test_analyze_data.py 补齐正式测试：
#   - 正常路径：tmp_path 下构造 CSV，断言输出含行数/列名/预览；
#   - 错误路径：文件不存在 / 不支持格式(.txt) / 空文件 / 损坏文件；
#   - 定向分析：question 命中趋势/分组对比/相关性三类关键词；
#   - filter_rows：eq/ne/gt/ge/lt/le/in、数值自动转型、非法操作符、列不存在；
#   - group_aggregate：六种聚合、非法聚合函数、非数值列、top_n 边界(0/100)；
#   - 截断：构造超宽/超长数据，断言输出长度 <= _MAX_OUTPUT_CHARS 且含"已截断"；
#   - pandas 3.0 兼容：字符串列(str dtype)被正确识别为类别列（回归 _is_category_like）；
#   - schema：四个工具的 args_schema 能生成合法 JSON Schema；
#   - 增强-编码容错：gbk 编码 CSV 能正常读取（回归 _read_csv_with_fallback）；
#   - 增强-日期识别：str 日期列自动转 datetime，趋势分析命中日期分支
#     （回归 _coerce_datetime_columns）；同时断言普通类别列（如城市名）不被误转；
#   - 增强-JSONL：.jsonl 文件可加载；
#   - 增强-图表：plot_chart 四种 kind 均生成 PNG 且文件非空、非法 kind 报错、
#     超 1000 点触发均匀采样；
#   - 增强-异步：asyncio.run(tool.ainvoke(...)) 与 .invoke(...) 结果一致。
