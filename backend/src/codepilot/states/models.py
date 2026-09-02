"""Pydantic 模型：State Bus 条目与 Harness 输出校验。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model


class FactEntryModel(BaseModel):
    """事实台账条目（来源 / 口径 / 取值 / 时间）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    source: str
    metric: str
    definition: str = ""
    value: str = ""
    url: str = ""
    snippet: str = ""
    timestamp: str = ""


class RuleEntryModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    domain: str
    content: str


class IssueEntryModel(BaseModel):
    """问题台账条目（风险 / 证据 / 修复 / 验收）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    source: str = ""
    risk: str
    fix: str
    evidence: str = ""
    status: str = "open"


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal: str
    scope: str = ""
    constraints: list[str] = Field(default_factory=list)


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    artifact_path: str = ""
    version: str = ""
    fix_notes: list[str] = Field(default_factory=list)


_TYPE_MAP: dict[str, type] = {
    "str": str,
    "string": str,
    "list": list,
    "dict": dict,
    "bool": bool,
    "int": int,
    "float": float,
}


def model_from_output_schema(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """把 agents/*.yaml 的 output_schema 变成可 with_structured_output 的模型。"""
    fields: dict[str, Any] = {}
    for key, raw in schema.items():
        typ = _TYPE_MAP.get(str(raw).strip().lower(), Any)
        fields[str(key)] = (typ | None, None)
    return create_model(f"{name}_HarnessOutput", **fields)


def validate_harness_output(name: str, schema: dict[str, Any], payload: Any) -> dict[str, Any]:
    """校验并规范化 Agent JSON 输出；失败则抛出 ValidationError。"""
    if not isinstance(payload, dict):
        raise ValueError("harness output must be a JSON object")
    model_cls = model_from_output_schema(name, schema)
    return model_cls.model_validate(payload).model_dump()
