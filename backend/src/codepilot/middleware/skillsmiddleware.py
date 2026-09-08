"""DeepAgent 技能目录中间件。

技能目录的扫描和系统提示词渲染具有截然不同的成本，因此拆分为两个
中间件：

* :class:`SkillCatalogLoaderMiddleware` 在 ``before_agent`` 中只执行一次，
  扫描技能根目录并将 ``SKILL.md`` 的轻量元数据写入私有状态；
* :class:`SkillCatalogPromptMiddleware` 在每次模型调用的请求边界渲染已缓存
  的元数据，临时追加一张技能目录卡片，不读取文件也不修改持久化消息。

这里使用 ``wrap_model_call``，而不是在 ``before_model`` 中向 ``messages``
追加系统消息：后者的返回值会写入图状态，导致每轮模型调用重复累积提示词。
``wrap_model_call`` 是 LangChain 用于修改 ``ModelRequest`` 的模型调用级钩子，
能保证渲染结果仅对当前调用生效。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, NotRequired, TypedDict

import yaml
from langchain.agents.middleware import (  # pyright: ignore[reportMissingImports]
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain.agents.middleware.types import (  # pyright: ignore[reportMissingImports]
    AgentState,
    ContextT,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_SKILL_FILENAME = "SKILL.md"
_MAX_SKILL_FILE_BYTES = 10 * 1024 * 1024
_MAX_SKILL_DESCRIPTION_LENGTH = 1_024
_MAX_RENDERED_SKILLS = 100
_FRONTMATTER_PATTERN = re.compile(r"\A---\s*\r?\n(?P<metadata>.*?)\r?\n---(?:\s*\r?\n|\Z)", re.DOTALL)


class SkillMetadata(TypedDict):
    """模型选择技能时所需的最小元数据。"""

    name: str
    description: str
    path: str


class SkillCatalogState(AgentState):
    """由技能中间件维护的私有状态。"""

    skills_metadata: NotRequired[Annotated[list[SkillMetadata], PrivateStateAttr]]
    skills_load_errors: NotRequired[Annotated[list[str], PrivateStateAttr]]


class SkillCatalogStateUpdate(TypedDict):
    """技能目录初始化写入的状态增量。"""

    skills_metadata: list[SkillMetadata]
    skills_load_errors: NotRequired[list[str]]


def _normalize_description(value: object) -> str:
    """将 YAML 描述规范为单行、受大小限制的目录文本。"""
    description = " ".join(str(value).split())
    return description[:_MAX_SKILL_DESCRIPTION_LENGTH]


def _parse_skill_metadata(skill_file: Path) -> SkillMetadata | None:
    """仅读取并解析单个 ``SKILL.md`` 的 YAML frontmatter。"""
    try:
        size = skill_file.stat().st_size
        if size > _MAX_SKILL_FILE_BYTES:
            logger.warning("Skipping oversized skill file %s (%d bytes)", skill_file, size)
            return None
        content = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Unable to read skill file %s: %s", skill_file, exc)
        return None

    match = _FRONTMATTER_PATTERN.match(content)
    if match is None:
        logger.warning("Skipping skill file without YAML frontmatter: %s", skill_file)
        return None

    try:
        frontmatter = yaml.safe_load(match.group("metadata"))
    except yaml.YAMLError as exc:
        logger.warning("Skipping skill file with invalid YAML frontmatter %s: %s", skill_file, exc)
        return None

    if not isinstance(frontmatter, Mapping):
        logger.warning("Skipping skill file with non-mapping frontmatter: %s", skill_file)
        return None

    name = str(frontmatter.get("name") or "").strip()
    description = _normalize_description(frontmatter.get("description") or "")
    if not name or not description:
        logger.warning("Skipping skill file missing name or description: %s", skill_file)
        return None

    return {"name": name, "description": description, "path": str(skill_file)}


class SkillCatalogLoaderMiddleware(AgentMiddleware[SkillCatalogState, Any, Any]):
    """在每个对话首次执行时加载技能元数据。

    ``skills_metadata`` 即使是空列表也代表已经扫描过，因而不会在同一会话的
    后续轮次再次触碰磁盘。技能源按照传入顺序加载；同名技能由后面的源覆盖。
    """

    state_schema = SkillCatalogState

    def __init__(self, sources: Sequence[str | Path]) -> None:
        self.sources = tuple(Path(source).expanduser().resolve() for source in sources)

    def _load_catalog(self) -> tuple[list[SkillMetadata], list[str]]:
        skills_by_name: dict[str, SkillMetadata] = {}
        errors: list[str] = []

        for source in self.sources:
            try:
                if not source.is_dir():
                    errors.append(f"技能目录不可用：{source}")
                    continue
                entries = sorted(source.iterdir(), key=lambda entry: entry.name.casefold())
            except OSError as exc:
                errors.append(f"无法扫描技能目录 {source}: {exc}")
                continue

            for entry in entries:
                if not entry.is_dir():
                    continue
                try:
                    resolved_entry = entry.resolve()
                    if not resolved_entry.is_relative_to(source):
                        errors.append(f"已忽略越出技能目录的符号链接：{entry}")
                        continue
                except OSError as exc:
                    errors.append(f"无法解析技能目录 {entry}: {exc}")
                    continue

                skill = _parse_skill_metadata(resolved_entry / _SKILL_FILENAME)
                if skill is None:
                    continue
                # 删除后再写入，既实现“后源覆盖前源”，又使目录呈现顺序可预测。
                skills_by_name.pop(skill["name"], None)
                skills_by_name[skill["name"]] = skill

        return list(skills_by_name.values()), errors

    def _load_once(self, state: SkillCatalogState) -> SkillCatalogStateUpdate | None:
        if "skills_metadata" in state:
            return None
        skills, errors = self._load_catalog()
        update: SkillCatalogStateUpdate = {"skills_metadata": skills}
        if errors:
            logger.warning("Skill catalog loading completed with errors: %s", errors)
            update["skills_load_errors"] = errors
        return update

    def before_agent(
        self,
        state: SkillCatalogState,
        runtime: Runtime[Any],
    ) -> SkillCatalogStateUpdate | None:
        """同步执行时，在对话开始前进行一次目录扫描。"""
        del runtime
        return self._load_once(state)

    async def abefore_agent(
        self,
        state: SkillCatalogState,
        runtime: Runtime[Any],
    ) -> SkillCatalogStateUpdate | None:
        """异步执行时将文件系统扫描移出事件循环。"""
        del runtime
        if "skills_metadata" in state:
            return None
        skills, errors = await asyncio.to_thread(self._load_catalog)
        update: SkillCatalogStateUpdate = {"skills_metadata": skills}
        if errors:
            logger.warning("Skill catalog loading completed with errors: %s", errors)
            update["skills_load_errors"] = errors
        return update


class SkillCatalogPromptMiddleware(AgentMiddleware[SkillCatalogState, ContextT, ResponseT]):
    """把已加载的技能元数据渲染为当前模型调用专属的系统提示词片段。"""

    state_schema = SkillCatalogState

    @staticmethod
    def render_catalog(skills: Sequence[SkillMetadata]) -> str:
        """纯内存地渲染技能目录卡片，不产生任何 I/O 或状态更新。"""
        if not skills:
            return (
                "## 可用技能目录\n\n"
                "当前对话未发现可用技能。若用户请求需要专用工作流，请按普通能力处理。"
            )

        lines = [
            "## 可用技能目录",
            "",
            (
                "以下仅是技能元数据。用户请求命中某项时，先读取对应 `SKILL.md` 的完整规则，"
                "再按规则执行；不要仅凭目录摘要猜测流程。"
            ),
            "",
        ]
        for skill in skills[:_MAX_RENDERED_SKILLS]:
            lines.extend(
                [
                    f"- **{skill['name']}**：{skill['description']}",
                    f"  - 指令文件：`{skill['path']}`",
                ]
            )
        omitted = len(skills) - _MAX_RENDERED_SKILLS
        if omitted > 0:
            lines.append(f"- 另有 {omitted} 个技能未在目录卡片中展开。")
        return "\n".join(lines)

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        """为当前调用构造附带目录卡片的新请求，不写回 ``messages``。"""
        state: Mapping[str, Any] = request.state or {}
        raw_skills = state.get("skills_metadata", [])
        skills = raw_skills if isinstance(raw_skills, list) else []
        catalog = self.render_catalog(skills)

        content_blocks = list(request.system_message.content_blocks) if request.system_message else []
        if content_blocks:
            catalog = f"\n\n{catalog}"
        content_blocks.append({"type": "text", "text": catalog})
        return request.override(system_message=SystemMessage(content_blocks=content_blocks))

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """在每次同步模型调用前注入已缓存的技能目录。"""
        return handler(self.modify_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        """在每次异步模型调用前注入已缓存的技能目录。"""
        return await handler(self.modify_request(request))


__all__ = [
    "SkillCatalogLoaderMiddleware",
    "SkillCatalogPromptMiddleware",
    "SkillCatalogState",
    "SkillMetadata",
]
