"""Macros system for Nyx.

Phase 15 adds trusted local Python macros that live either in a global Nyx
macros directory or under ``~/notes/projects/<project>/macros``. Macros are
AI-generatable, user-editable Python files whose module docstring defines
stable metadata such as name, triggers, scope, and description.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig
from nyx.macros import (
    MacroContext,
    MacroDefinition,
    discover_macros,
    execute_macro,
    parse_macro_definition_source,
)
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_MACRO_PATTERNS = (
    re.compile(r"\bmacro\b", re.IGNORECASE),
    re.compile(r"\bmacros\b", re.IGNORECASE),
    re.compile(r"\bautomation\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_PYTHON_BLOCK_PATTERN = re.compile(r"```(?:python)?\s*(.*?)\s*```", re.DOTALL)
_ALLOWED_OPERATIONS = {"list_macros", "show_macro", "run_macro", "create_macro", "reject"}
_FORBIDDEN_SOURCE_PATTERNS = (
    "hyprctl",
    "grim",
    "brightnessctl",
    "wpctl",
    "notify-send",
    "subprocess.run",
    "os.system",
)


@dataclass(slots=True)
class MacroPlan:
    """Validated provider-produced plan for one macro action."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class MacrosResult:
    """Structured result returned by the Phase 15 macros module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class MacrosModule:
    """Manage AI-generated and user-editable global/project Python macros."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the macros module with config, bridge, and providers."""

        self.config = config
        self.bridge = bridge
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.macros")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious macro request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _MACRO_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> MacrosResult:
        """Handle one explicit macro request."""

        await self._ensure_layout()
        macros = await self._discover_macros()
        project_names = await self._list_project_names()
        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, macros, project_names),
            context=self._planner_context(macros, project_names),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Macros planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        if plan.operation == "list_macros":
            scope = self._optional_string_argument(plan.arguments, "scope") or "all"
            project_name = self._optional_string_argument(plan.arguments, "project")
            filtered = self._filter_macros(macros, scope=scope, project_name=project_name)
            if not filtered:
                return self._result_from_provider(
                    provider_result,
                    "No macros found for that scope.",
                    plan.operation,
                )
            return self._result_from_provider(
                provider_result,
                self._format_macro_list(filtered),
                plan.operation,
            )

        macro_name = self._require_string_argument(plan.arguments, "name")
        scope = self._optional_string_argument(plan.arguments, "scope") or "all"
        project_name = self._optional_string_argument(plan.arguments, "project")

        if plan.operation == "show_macro":
            macro = self._resolve_macro(macros, macro_name, scope=scope, project_name=project_name)
            if macro is None:
                return self._result_from_provider(
                    provider_result,
                    f"Nyx could not find a macro named '{macro_name}'.",
                    plan.operation,
                )
            source = await asyncio.to_thread(macro.file_path.read_text, encoding="utf-8")
            return self._result_from_provider(
                provider_result,
                f"Macro '{macro.name}' at {macro.file_path}:\n\n{source}",
                plan.operation,
            )

        if plan.operation == "run_macro":
            macro = self._resolve_macro(macros, macro_name, scope=scope, project_name=project_name)
            if macro is None:
                return self._result_from_provider(
                    provider_result,
                    f"Nyx could not find a macro named '{macro_name}'.",
                    plan.operation,
                )
            await self._validate_macro_file(macro.file_path)
            arguments = self._optional_string_argument(plan.arguments, "arguments")
            context = MacroContext(
                config=self.config,
                bridge=self.bridge,
                logger=self.logger,
                request_text=request_text,
                macro=macro,
                arguments=arguments,
                project_name=macro.project_name,
            )
            response_text = await execute_macro(macro, context)
            return self._result_from_provider(provider_result, response_text, plan.operation)

        create_scope = self._require_scope(plan.arguments)
        create_project = self._optional_string_argument(plan.arguments, "project")
        if create_scope == "project":
            if create_project is None:
                return self._result_from_provider(
                    provider_result,
                    "Project-scoped macros must specify an existing project.",
                    plan.operation,
                )
            resolved_project = await self._resolve_project_name(create_project)
            if resolved_project is None:
                return self._result_from_provider(
                    provider_result,
                    f"Nyx could not find a project named '{create_project}'.",
                    plan.operation,
                )
            create_project = resolved_project
        else:
            create_project = None

        description = self._require_string_argument(plan.arguments, "description")
        triggers = self._optional_string_list(plan.arguments, "triggers")
        target_path = self._macro_path(
            self._slugify_name(macro_name),
            scope=create_scope,
            project_name=create_project,
        )
        if target_path.exists():
            return self._result_from_provider(
                provider_result,
                f"Macro '{macro_name}' already exists at {target_path}.",
                plan.operation,
            )

        generator_result = await self.provider_registry.query(
            prompt=self._build_generator_prompt(
                name=macro_name,
                description=description,
                scope=create_scope,
                project_name=create_project,
                triggers=triggers,
            ),
            context=self._planner_context(macros, project_names),
            preferred_provider_name=model_override,
        )
        try:
            source = self._extract_python_source(generator_result.text)
            self._validate_macro_source(source)
            created_macro = self._parse_generated_macro(
                source,
                target_path=target_path,
                project_name=create_project,
            )
        except Exception as exc:
            return MacrosResult(
                response_text=f"Nyx could not generate a valid macro: {exc}",
                used_model=generator_result.provider_name,
                model_name=generator_result.model_name,
                token_count=generator_result.token_count,
                degraded=True,
                operation=plan.operation,
            )
        await self._write_macro(target_path, source)
        response_text = (
            f"Created {created_macro.scope} macro '{created_macro.name}' at {created_macro.file_path}."
        )
        if created_macro.triggers:
            response_text += f" Triggers: {', '.join(created_macro.triggers)}."
        return MacrosResult(
            response_text=response_text,
            used_model=generator_result.provider_name,
            model_name=generator_result.model_name,
            token_count=generator_result.token_count,
            degraded=provider_result.fallback_used or generator_result.fallback_used,
            operation=plan.operation,
        )

    def _planner_context(
        self,
        macros: list[MacroDefinition],
        project_names: list[str],
    ) -> dict[str, Any]:
        """Return static planning context for macro requests."""

        return {
            "module": "macros",
            "global_macros_dir": str(self._global_macros_dir()),
            "projects_dir": str(self.config.notes.projects_dir),
            "existing_projects": project_names,
            "existing_macros": [
                {
                    "name": macro.name,
                    "scope": macro.scope,
                    "project": macro.project_name,
                    "triggers": macro.triggers,
                }
                for macro in macros
            ],
        }

    def _build_planner_prompt(
        self,
        request_text: str,
        macros: list[MacroDefinition],
        project_names: list[str],
    ) -> str:
        """Build the provider prompt for one macro request."""

        macro_names = ", ".join(
            f"{macro.name} ({macro.scope}{f'/{macro.project_name}' if macro.project_name else ''})"
            for macro in macros
        ) or "(none)"
        project_list = ", ".join(project_names) if project_names else "(none)"
        return (
            "You are Nyx's Phase 15 macros planner. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: list_macros, show_macro, run_macro, create_macro, reject. "
            f"Existing projects: {project_list}. Existing macros: {macro_names}. "
            "Use create_macro only when the user explicitly wants a new macro. "
            "Use show_macro to inspect macro source. Use run_macro to execute an existing macro. "
            "If the request is not a macro action, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- list_macros: {"scope": "all"|"global"|"project", "project": str|null}\n'
            '- show_macro: {"name": str, "scope": "all"|"global"|"project", "project": str|null}\n'
            '- run_macro: {"name": str, "scope": "all"|"global"|"project", "project": str|null, "arguments": str|null}\n'
            '- create_macro: {"name": str, "scope": "global"|"project", "project": str|null, "description": str, "triggers": [str]}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _build_generator_prompt(
        self,
        *,
        name: str,
        description: str,
        scope: str,
        project_name: str | None,
        triggers: list[str],
    ) -> str:
        """Build the provider prompt that generates one macro Python file."""

        trigger_list = ", ".join(triggers) if triggers else name
        project_text = project_name or "(none)"
        return (
            "Write exactly one complete Python module for a Nyx macro. "
            "Return code only. No markdown fences unless you must; no explanation.\n\n"
            "Requirements:\n"
            "- Add a top-level module docstring with exactly these metadata keys on separate lines:\n"
            f"  name: {name}\n"
            f"  triggers: {trigger_list}\n"
            f"  scope: {scope}\n"
            f"  description: {description}\n"
            "- Define a function named run(context).\n"
            "- run(context) may be sync or async and must return str or None.\n"
            "- Use only stdlib imports.\n"
            "- If the macro needs system actions, use context.bridge. Never call hyprctl, grim, wpctl, brightnessctl, notify-send, os.system, or subprocess.run directly.\n"
            "- Keep the code concise and readable.\n"
            "- The macro must be valid Python 3.11+.\n\n"
            f"Macro scope: {scope}\n"
            f"Linked project: {project_text}\n"
            f"Macro description: {description}\n"
        )

    async def _ensure_layout(self) -> None:
        """Ensure the global and project macro roots exist."""

        def _sync_ensure() -> None:
            self._global_macros_dir().mkdir(parents=True, exist_ok=True)
            self.config.notes.projects_dir.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(_sync_ensure)

    async def _discover_macros(self) -> list[MacroDefinition]:
        """Return all loadable macros from global and project locations."""

        return await discover_macros(self._global_macros_dir(), self.config.notes.projects_dir)

    async def _list_project_names(self) -> list[str]:
        """Return existing project directory names."""

        def _sync_list() -> list[str]:
            if not self.config.notes.projects_dir.exists():
                return []
            return sorted(
                child.name
                for child in self.config.notes.projects_dir.iterdir()
                if child.is_dir()
            )

        return await asyncio.to_thread(_sync_list)

    async def _resolve_project_name(self, project_name: str) -> str | None:
        """Resolve a case-insensitive project directory name."""

        for candidate in await self._list_project_names():
            if candidate.casefold() == project_name.casefold():
                return candidate
        return None

    def _filter_macros(
        self,
        macros: list[MacroDefinition],
        *,
        scope: str,
        project_name: str | None,
    ) -> list[MacroDefinition]:
        """Filter macros by scope and optional project name."""

        normalized_scope = scope.lower()
        if normalized_scope not in {"all", "global", "project"}:
            raise ValueError(f"Unsupported macro scope: {scope!r}")

        filtered = macros
        if normalized_scope != "all":
            filtered = [macro for macro in filtered if macro.scope == normalized_scope]
        if project_name:
            filtered = [
                macro for macro in filtered if (macro.project_name or "").casefold() == project_name.casefold()
            ]
        return filtered

    def _resolve_macro(
        self,
        macros: list[MacroDefinition],
        name: str,
        *,
        scope: str,
        project_name: str | None,
    ) -> MacroDefinition | None:
        """Resolve one macro by user-facing name or file stem."""

        candidates = self._filter_macros(macros, scope=scope, project_name=project_name)
        normalized = name.casefold()
        for macro in candidates:
            if macro.name.casefold() == normalized or macro.file_path.stem.casefold() == normalized:
                return macro
        return None

    def _global_macros_dir(self) -> Path:
        """Return the global macros directory under the Nyx config root."""

        return self.config.config_path.parent / "macros"

    def _macro_path(self, slug: str, *, scope: str, project_name: str | None) -> Path:
        """Return the target file path for a generated macro."""

        if scope == "global":
            return self._global_macros_dir() / f"{slug}.py"
        assert project_name is not None
        return self.config.notes.projects_dir / project_name / "macros" / f"{slug}.py"

    async def _write_macro(self, target_path: Path, source: str) -> None:
        """Persist one generated macro to disk."""

        def _sync_write() -> None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(source.rstrip() + "\n", encoding="utf-8")

        await asyncio.to_thread(_sync_write)

    async def _validate_macro_file(self, path: Path) -> None:
        """Validate one saved macro before executing it."""

        source = await asyncio.to_thread(path.read_text, encoding="utf-8")
        self._validate_macro_source(source)
        self._parse_generated_macro(source, target_path=path, project_name=None)

    async def _load_macro(self, path: Path, project_name: str | None) -> MacroDefinition:
        """Load one persisted macro definition from disk."""

        from nyx.macros.runtime import _load_macro_definition

        return await _load_macro_definition(path, project_name)

    def _parse_generated_macro(
        self,
        source: str,
        *,
        target_path: Path,
        project_name: str | None,
    ) -> MacroDefinition:
        """Validate generated macro source before it is written to disk."""

        return parse_macro_definition_source(
            source,
            file_path=target_path,
            project_name=project_name,
        )

    def _format_macro_list(self, macros: list[MacroDefinition]) -> str:
        """Render one concise macro listing."""

        lines = ["Available macros:"]
        for macro in macros:
            scope_label = macro.scope if macro.scope == "global" else f"project/{macro.project_name}"
            trigger_text = ", ".join(macro.triggers)
            lines.append(
                f"- {macro.name} [{scope_label}] — {macro.description} (triggers: {trigger_text})"
            )
        return "\n".join(lines)

    def _parse_plan(self, planner_text: str) -> MacroPlan:
        """Parse and validate the JSON macro plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Macros planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported macros operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Macros planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Macros planner 'rationale' must be a string when present.")

        return MacroPlan(operation=operation, arguments=arguments, rationale=rationale)

    def _extract_json_object(self, text: str) -> str:
        """Extract one JSON object from raw provider output."""

        fenced_match = _JSON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        object_match = _JSON_OBJECT_PATTERN.search(text)
        if object_match is None:
            raise ValueError("Macros planner did not return a JSON object.")
        return object_match.group(0).strip()

    def _extract_python_source(self, text: str) -> str:
        """Extract a Python module body from provider output."""

        fenced_match = _PYTHON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()
        stripped = text.strip()
        for marker in ('"""', "'''"):
            marker_index = stripped.find(marker)
            if marker_index != -1:
                return stripped[marker_index:].strip()
        return stripped

    def _validate_macro_source(self, source: str) -> None:
        """Validate generated macro code before writing it to disk."""

        for forbidden in _FORBIDDEN_SOURCE_PATTERNS:
            if forbidden in source:
                raise ValueError(
                    f"Generated macro source used forbidden OS-specific call '{forbidden}'."
                )

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required non-empty string argument."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Macro action is missing string argument '{key}'.")
        return value.strip()

    def _optional_string_argument(self, arguments: dict[str, Any], key: str) -> str | None:
        """Return one optional string argument when present."""

        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Macro action argument '{key}' must be a string when present.")
        normalized = value.strip()
        return normalized or None

    def _optional_string_list(self, arguments: dict[str, Any], key: str) -> list[str]:
        """Return one optional string-list argument when present."""

        value = arguments.get(key)
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Macro action argument '{key}' must be a list of strings.")
        return [item.strip() for item in value if item.strip()]

    def _require_scope(self, arguments: dict[str, Any]) -> str:
        """Return one required macro scope."""

        scope = self._require_string_argument(arguments, "scope").lower()
        if scope not in {"global", "project"}:
            raise ValueError(f"Unsupported macro scope: {scope!r}")
        return scope

    def _slugify_name(self, name: str) -> str:
        """Convert one user-facing macro name into a filesystem-safe stem."""

        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        if not slug:
            raise ValueError("Macro name must contain at least one alphanumeric character.")
        return slug

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
        *,
        degraded: bool | None = None,
    ) -> MacrosResult:
        """Build a macros result while preserving provider metadata."""

        return MacrosResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used if degraded is None else degraded,
            operation=operation,
        )
