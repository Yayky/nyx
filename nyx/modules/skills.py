"""Skills system for Nyx.

Phase 16 adds auto-discovered global skills under ``~/.config/nyx/skills``.
Skills are user-authored Python modules with metadata docstrings and a
``run(context)`` entrypoint. Nyx supports the four documented trigger modes:
keyword, explicit, AI intent, and scheduled.
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
from nyx.skills import SkillContext, SkillDefinition, discover_skills, execute_skill
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_EXPLICIT_PATTERNS = (
    re.compile(r"\b(?:run|use|execute)\s+(?:the\s+)?skill\s+(?P<name>.+)$", re.IGNORECASE),
    re.compile(r"\b(?:run|use|execute)\s+(?P<name>.+?)\s+skill\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {"run_skill", "reject"}
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
class SkillPlan:
    """Validated provider-produced plan for AI-intent skill selection."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class SkillsResult:
    """Structured result returned by the Phase 16 skills module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class SkillsModule:
    """Auto-discover and execute Nyx user-defined skills."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the skills module with explicit runtime dependencies."""

        self.config = config
        self.bridge = bridge
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.skills")

    async def maybe_handle(
        self,
        request_text: str,
        model_override: str | None = None,
    ) -> SkillsResult | None:
        """Try to handle one request through the Phase 16 skills system."""

        skills = await self._discover_skills()
        if not skills:
            return None

        explicit_match = self._extract_explicit_name(request_text)
        if explicit_match is not None:
            skill = self._resolve_skill(skills, explicit_match, trigger_mode="explicit")
            if skill is None:
                return SkillsResult(
                    response_text=f"Nyx could not find an enabled skill named '{explicit_match}'.",
                    used_model=model_override or self.config.models.default,
                    model_name=None,
                    token_count=None,
                    degraded=True,
                    operation="run_skill",
                )
            response_text = await self._execute_skill(
                skill,
                request_text=request_text,
                trigger_mode="explicit",
                arguments=None,
            )
            return SkillsResult(
                response_text=response_text,
                used_model=model_override or self.config.models.default,
                model_name=None,
                token_count=None,
                degraded=False,
                operation="run_skill",
            )

        keyword_match = self._match_keyword_skill(skills, request_text)
        if keyword_match is not None:
            response_text = await self._execute_skill(
                keyword_match,
                request_text=request_text,
                trigger_mode="keyword",
                arguments=None,
            )
            return SkillsResult(
                response_text=response_text,
                used_model=model_override or self.config.models.default,
                model_name=None,
                token_count=None,
                degraded=False,
                operation="run_skill",
            )

        ai_intent_skills = [skill for skill in skills if "ai_intent" in skill.trigger_modes]
        if not ai_intent_skills:
            return None

        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, ai_intent_skills),
            context=self._planner_context(ai_intent_skills),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Skills planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )
        if plan.operation == "reject":
            return None

        skill_name = self._require_string_argument(plan.arguments, "name")
        arguments = self._optional_string_argument(plan.arguments, "arguments")
        skill = self._resolve_skill(ai_intent_skills, skill_name, trigger_mode="ai_intent")
        if skill is None:
            return SkillsResult(
                response_text=f"Nyx could not find an enabled AI-intent skill named '{skill_name}'.",
                used_model=provider_result.provider_name,
                model_name=provider_result.model_name,
                token_count=provider_result.token_count,
                degraded=True,
                operation="run_skill",
            )

        response_text = await self._execute_skill(
            skill,
            request_text=request_text,
            trigger_mode="ai_intent",
            arguments=arguments,
        )
        return SkillsResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used,
            operation="run_skill",
        )

    async def _discover_skills(self) -> list[SkillDefinition]:
        """Return all enabled discovered skills."""

        return await discover_skills(
            self.config.config_path.parent / "skills",
            disabled_names=set(self.config.skills.disabled),
        )

    def _planner_context(self, skills: list[SkillDefinition]) -> dict[str, Any]:
        """Return context exposed to AI-intent skill planning."""

        return {
            "module": "skills",
            "skills_dir": str(self.config.config_path.parent / "skills"),
            "enabled_skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": skill.triggers,
                    "trigger_modes": skill.trigger_modes,
                }
                for skill in skills
            ],
        }

    def _build_planner_prompt(self, request_text: str, skills: list[SkillDefinition]) -> str:
        """Build the provider prompt for AI-intent skill selection."""

        skill_list = "; ".join(
            f"{skill.name}: {skill.description} (triggers: {', '.join(skill.triggers)})"
            for skill in skills
        ) or "(none)"
        return (
            "You are Nyx's Phase 16 skills planner. "
            "Choose whether one enabled skill should handle the user request. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: run_skill, reject. "
            f"Enabled AI-intent skills: {skill_list}. "
            "Use run_skill only when one listed skill clearly fits better than a normal chat response. "
            "If none fit, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- run_skill: {"name": str, "arguments": str|null}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    async def _execute_skill(
        self,
        skill: SkillDefinition,
        *,
        request_text: str | None,
        trigger_mode: str,
        arguments: str | None,
    ) -> str:
        """Validate and execute one resolved skill."""

        await self._validate_skill_file(skill.file_path)
        return await execute_skill(
            skill,
            SkillContext(
                config=self.config,
                bridge=self.bridge,
                logger=self.logger,
                request_text=request_text,
                skill=skill,
                trigger_mode=trigger_mode,
                arguments=arguments,
            ),
        )

    async def _validate_skill_file(self, path: Path) -> None:
        """Validate one saved skill before executing it."""

        source = await asyncio.to_thread(path.read_text, encoding="utf-8")
        for forbidden in _FORBIDDEN_SOURCE_PATTERNS:
            if forbidden in source:
                raise ValueError(
                    f"Skill source used forbidden OS-specific call '{forbidden}'."
                )

    def _extract_explicit_name(self, request_text: str) -> str | None:
        """Return an explicitly requested skill name when present."""

        stripped = request_text.strip()
        for pattern in _EXPLICIT_PATTERNS:
            match = pattern.search(stripped)
            if match is None:
                continue
            name = match.group("name").strip().strip("\"'")
            return name or None
        return None

    def _match_keyword_skill(self, skills: list[SkillDefinition], request_text: str) -> SkillDefinition | None:
        """Return the first keyword-triggered skill that matches the request."""

        lowered = request_text.casefold()
        for skill in skills:
            if "keyword" not in skill.trigger_modes:
                continue
            for trigger in skill.triggers:
                if trigger.casefold() in lowered:
                    return skill
        return None

    def _resolve_skill(
        self,
        skills: list[SkillDefinition],
        name: str,
        *,
        trigger_mode: str,
    ) -> SkillDefinition | None:
        """Resolve one skill by name or trigger within the allowed trigger mode."""

        normalized = name.casefold()
        for skill in skills:
            if trigger_mode not in skill.trigger_modes:
                continue
            if skill.name.casefold() == normalized:
                return skill
            if any(trigger.casefold() == normalized for trigger in skill.triggers):
                return skill
        return None

    def _parse_plan(self, planner_text: str) -> SkillPlan:
        """Parse and validate the JSON AI-intent skill plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Skills planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported skills operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Skills planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Skills planner 'rationale' must be a string when present.")

        return SkillPlan(operation=operation, arguments=arguments, rationale=rationale)

    def _extract_json_object(self, text: str) -> str:
        """Extract a JSON object from raw provider output."""

        fenced_match = _JSON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        object_match = _JSON_OBJECT_PATTERN.search(text)
        if object_match is None:
            raise ValueError("Skills planner did not return a JSON object.")
        return object_match.group(0).strip()

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required non-empty string argument."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Skills action is missing string argument '{key}'.")
        return value.strip()

    def _optional_string_argument(self, arguments: dict[str, Any], key: str) -> str | None:
        """Return one optional string argument when present."""

        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Skills action argument '{key}' must be a string when present.")
        normalized = value.strip()
        return normalized or None
