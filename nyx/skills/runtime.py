"""Skill discovery and execution support for Nyx.

Phase 16 adds auto-discovered user-defined skills stored under
``~/.config/nyx/skills/*.py``. Skills are ordinary Python modules with a small
metadata docstring and a ``run(context)`` entrypoint. Nyx supports four trigger
modes from the architecture doc: keyword, explicit, AI intent, and scheduled.
"""

from __future__ import annotations

import asyncio
import ast
from dataclasses import dataclass
import hashlib
import inspect
import logging
from pathlib import Path
import sys
import types
from typing import Any

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig

_ALLOWED_TRIGGER_MODES = {"keyword", "explicit", "ai_intent", "scheduled"}


@dataclass(slots=True)
class SkillDefinition:
    """Parsed metadata for one discovered skill script."""

    name: str
    description: str
    triggers: list[str]
    trigger_modes: list[str]
    file_path: Path
    schedule_seconds: int | None = None


@dataclass(slots=True)
class SkillContext:
    """Runtime context passed into one skill's ``run`` entrypoint."""

    config: NyxConfig
    bridge: SystemBridge
    logger: logging.Logger
    request_text: str | None
    skill: SkillDefinition
    trigger_mode: str
    arguments: str | None = None


async def discover_skills(
    skills_dir: Path,
    *,
    disabled_names: set[str] | None = None,
) -> list[SkillDefinition]:
    """Discover and parse all enabled skills from the configured directory."""

    definitions: list[SkillDefinition] = []
    disabled = {name.casefold() for name in (disabled_names or set())}
    if not skills_dir.exists():
        return []

    for file_path in sorted(skills_dir.glob("*.py")):
        try:
            definition = await _load_skill_definition(file_path)
        except RuntimeError:
            continue
        if definition.name.casefold() in disabled or file_path.stem.casefold() in disabled:
            continue
        definitions.append(definition)
    return definitions


async def execute_skill(skill: SkillDefinition, context: SkillContext) -> str:
    """Load and execute one skill script's ``run`` entrypoint."""

    module_name = _module_name_for_path(skill.file_path)
    source = await asyncio.to_thread(skill.file_path.read_text, encoding="utf-8")
    code = compile(source, str(skill.file_path), "exec")
    module = types.ModuleType(module_name)
    module.__file__ = str(skill.file_path)
    sys.modules[module_name] = module
    try:
        exec(code, module.__dict__)
        run_callable = getattr(module, "run", None)
        if not callable(run_callable):
            raise RuntimeError(
                f"Skill '{skill.name}' does not define a callable run(context) entrypoint."
            )
        result = run_callable(context)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            return f"Skill '{skill.name}' completed."
        if not isinstance(result, str):
            raise RuntimeError(
                f"Skill '{skill.name}' returned {type(result).__name__}, expected str or None."
            )
        normalized = result.strip()
        return normalized or f"Skill '{skill.name}' completed."
    finally:
        sys.modules.pop(module_name, None)


async def _load_skill_definition(file_path: Path) -> SkillDefinition:
    """Parse one Python file into a ``SkillDefinition``."""

    source = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
    return parse_skill_definition_source(source, file_path=file_path)


def parse_skill_definition_source(source: str, *, file_path: Path) -> SkillDefinition:
    """Parse one Python source string into a ``SkillDefinition``."""

    try:
        parsed = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        raise RuntimeError(f"Skill file {file_path} is not valid Python: {exc}") from exc

    raw_docstring = ast.get_docstring(parsed)
    if not raw_docstring:
        raise RuntimeError(f"Skill file {file_path} is missing its metadata docstring.")

    metadata = _parse_metadata_docstring(raw_docstring, file_path)
    return SkillDefinition(
        name=metadata["name"],
        description=metadata["description"],
        triggers=metadata["triggers"],
        trigger_modes=metadata["trigger_modes"],
        file_path=file_path,
        schedule_seconds=metadata["schedule_seconds"],
    )


def _parse_metadata_docstring(docstring: str, file_path: Path) -> dict[str, Any]:
    """Parse the simple key-value metadata format stored in a skill docstring."""

    parsed: dict[str, str] = {}
    for raw_line in docstring.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip().lower()] = value.strip()

    required = {"name", "description", "triggers", "trigger_modes"}
    missing = required - set(parsed)
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise RuntimeError(f"Skill file {file_path} is missing metadata keys: {missing_keys}")

    triggers = [item.strip() for item in parsed["triggers"].split(",") if item.strip()]
    if not triggers:
        raise RuntimeError(f"Skill file {file_path} must define at least one trigger.")

    trigger_modes = [
        item.strip().lower().replace("ai intent", "ai_intent")
        for item in parsed["trigger_modes"].split(",")
        if item.strip()
    ]
    if not trigger_modes:
        raise RuntimeError(f"Skill file {file_path} must define at least one trigger mode.")
    unsupported = set(trigger_modes) - _ALLOWED_TRIGGER_MODES
    if unsupported:
        unsupported_modes = ", ".join(sorted(unsupported))
        raise RuntimeError(
            f"Skill file {file_path} uses unsupported trigger modes: {unsupported_modes}"
        )

    raw_schedule = parsed.get("schedule_seconds")
    schedule_seconds: int | None = None
    if raw_schedule is not None:
        try:
            schedule_seconds = int(raw_schedule)
        except ValueError as exc:
            raise RuntimeError(
                f"Skill file {file_path} has invalid schedule_seconds={raw_schedule!r}"
            ) from exc
        if schedule_seconds <= 0:
            raise RuntimeError(f"Skill file {file_path} must use a positive schedule_seconds.")
    elif "scheduled" in trigger_modes:
        raise RuntimeError(
            f"Skill file {file_path} uses scheduled trigger mode but omits schedule_seconds."
        )

    return {
        "name": parsed["name"],
        "description": parsed["description"],
        "triggers": triggers,
        "trigger_modes": trigger_modes,
        "schedule_seconds": schedule_seconds,
    }


def _module_name_for_path(file_path: Path) -> str:
    """Return a stable synthetic module name for one skill file path."""

    digest = hashlib.sha256(str(file_path).encode("utf-8")).hexdigest()[:12]
    return f"nyx_user_skill_{digest}"
