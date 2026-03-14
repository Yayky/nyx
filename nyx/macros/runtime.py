"""Macro discovery and execution support for Nyx.

Phase 15 introduces local Python macros that are either global to Nyx or linked
to one project under ``~/notes/projects/<project>/macros``. Each macro stores
its metadata in the module docstring and exposes a ``run(context)`` entrypoint
that Nyx invokes in-process so trusted local code can still interact with
``SystemBridge`` and the loaded config instead of bypassing Nyx.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ast
import hashlib
import inspect
import logging
from pathlib import Path
import sys
import types
from typing import Any

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig


@dataclass(slots=True)
class MacroDefinition:
    """Parsed metadata for one stored macro script.

    Attributes:
        name: Stable user-facing macro name.
        triggers: Free-form phrases describing what the macro is for.
        scope: Either ``global`` or ``project``.
        description: Human-readable explanation shown in listings.
        file_path: Concrete Python script path.
        project_name: Associated project when scope is ``project``.
    """

    name: str
    triggers: list[str]
    scope: str
    description: str
    file_path: Path
    project_name: str | None = None


@dataclass(slots=True)
class MacroContext:
    """Runtime context passed into one macro's ``run`` entrypoint.

    Attributes:
        config: Loaded Nyx configuration.
        bridge: Active system bridge implementation so macros can perform
            system actions without bypassing the bridge boundary.
        logger: Logger dedicated to the macro runtime.
        request_text: Raw user request that triggered the macro.
        macro: Parsed macro metadata for the active script.
        arguments: Optional free-form arguments extracted by the planner.
        project_name: Resolved project name for project-scoped macros.
    """

    config: NyxConfig
    bridge: SystemBridge
    logger: logging.Logger
    request_text: str
    macro: MacroDefinition
    arguments: str | None = None
    project_name: str | None = None


async def discover_macros(global_dir: Path, projects_dir: Path) -> list[MacroDefinition]:
    """Discover and parse all valid macros from global and project directories."""

    definitions: list[MacroDefinition] = []
    if global_dir.exists():
        for file_path in sorted(global_dir.glob("*.py")):
            try:
                definitions.append(await _load_macro_definition(file_path, None))
            except RuntimeError:
                continue

    if projects_dir.exists():
        for project_dir in sorted(child for child in projects_dir.iterdir() if child.is_dir()):
            macros_dir = project_dir / "macros"
            if not macros_dir.exists():
                continue
            for file_path in sorted(macros_dir.glob("*.py")):
                try:
                    definitions.append(await _load_macro_definition(file_path, project_dir.name))
                except RuntimeError:
                    continue

    return definitions


async def execute_macro(macro: MacroDefinition, context: MacroContext) -> str:
    """Load and execute one macro script's ``run`` entrypoint."""

    module_name = _module_name_for_path(macro.file_path)
    source = await asyncio.to_thread(macro.file_path.read_text, encoding="utf-8")
    code = compile(source, str(macro.file_path), "exec")
    module = types.ModuleType(module_name)
    module.__file__ = str(macro.file_path)
    sys.modules[module_name] = module
    try:
        exec(code, module.__dict__)
        run_callable = getattr(module, "run", None)
        if not callable(run_callable):
            raise RuntimeError(
                f"Macro '{macro.name}' does not define a callable run(context) entrypoint."
            )

        result = run_callable(context)
        if inspect.isawaitable(result):
            result = await result

        if result is None:
            return f"Macro '{macro.name}' completed."
        if not isinstance(result, str):
            raise RuntimeError(
                f"Macro '{macro.name}' returned {type(result).__name__}, expected str or None."
            )
        normalized = result.strip()
        return normalized or f"Macro '{macro.name}' completed."
    finally:
        sys.modules.pop(module_name, None)


async def _load_macro_definition(file_path: Path, project_name: str | None) -> MacroDefinition:
    """Parse one Python file into a ``MacroDefinition``."""

    source = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
    return parse_macro_definition_source(source, file_path=file_path, project_name=project_name)


def parse_macro_definition_source(
    source: str,
    *,
    file_path: Path,
    project_name: str | None,
) -> MacroDefinition:
    """Parse one Python source string into a ``MacroDefinition``."""

    try:
        parsed = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        raise RuntimeError(f"Macro file {file_path} is not valid Python: {exc}") from exc

    raw_docstring = ast.get_docstring(parsed)
    if not raw_docstring:
        raise RuntimeError(f"Macro file {file_path} is missing its metadata docstring.")

    metadata = _parse_metadata_docstring(raw_docstring, file_path)
    scope = metadata["scope"]
    return MacroDefinition(
        name=metadata["name"],
        triggers=metadata["triggers"],
        scope=scope,
        description=metadata["description"],
        file_path=file_path,
        project_name=project_name if scope == "project" else None,
    )


def _parse_metadata_docstring(docstring: str, file_path: Path) -> dict[str, Any]:
    """Parse the simple key-value metadata format stored in a macro docstring."""

    parsed: dict[str, str] = {}
    for raw_line in docstring.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip().lower()] = value.strip()

    required = {"name", "triggers", "scope", "description"}
    missing = required - set(parsed)
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise RuntimeError(f"Macro file {file_path} is missing metadata keys: {missing_keys}")

    scope = parsed["scope"].lower()
    if scope not in {"global", "project"}:
        raise RuntimeError(
            f"Macro file {file_path} has unsupported scope {parsed['scope']!r}; use global or project."
        )

    triggers = [item.strip() for item in parsed["triggers"].split(",") if item.strip()]
    if not triggers:
        raise RuntimeError(f"Macro file {file_path} must define at least one trigger.")

    return {
        "name": parsed["name"],
        "triggers": triggers,
        "scope": scope,
        "description": parsed["description"],
    }


def _module_name_for_path(file_path: Path) -> str:
    """Return a stable synthetic module name for one macro file path."""

    digest = hashlib.sha256(str(file_path).encode("utf-8")).hexdigest()[:12]
    return f"nyx_user_macro_{digest}"
