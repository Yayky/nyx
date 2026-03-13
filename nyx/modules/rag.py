"""Explicit semantic-search module for Nyx's Phase 8 RAG system."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from nyx.config import NyxConfig
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry
from nyx.rag.service import RagService
from nyx.rag.store import RagSearchHit

_RAG_PATTERNS = (
    re.compile(r"\bsearch\b.+\b(note|notes|project|projects|inbox)\b", re.IGNORECASE),
    re.compile(r"\bfind\b.+\b(note|notes|project|projects|inbox)\b", re.IGNORECASE),
    re.compile(r"\bwhat do my\b.+\b(notes|projects)\b.+\bsay about\b", re.IGNORECASE),
    re.compile(r"\blookup\b.+\b(note|notes|project|projects)\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {"search_notes", "search_project", "search_inbox", "reject"}


@dataclass(slots=True)
class RagPlan:
    """Validated provider-produced search plan for one RAG request."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class RagResult:
    """Structured result returned by the Phase 8 RAG module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class RagModule:
    """Plan and execute explicit local semantic-search requests."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        rag_service: RagService,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit RAG and provider dependencies."""

        self.config = config
        self.provider_registry = provider_registry
        self.rag_service = rag_service
        self.logger = logger or logging.getLogger("nyx.modules.rag")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether a prompt is an obvious explicit RAG lookup request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _RAG_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> RagResult:
        """Search the local RAG index for one explicit notes/projects query."""

        project_names = await self.rag_service.list_project_names()
        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, project_names),
            context=self._planner_context(project_names),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "RAG planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        query = self._require_string_argument(plan.arguments, "query")
        if plan.operation == "search_project":
            project = self._require_string_argument(plan.arguments, "project")
            resolved = await self.rag_service.resolve_project_name(project)
            if resolved is None:
                return self._result_from_provider(
                    provider_result,
                    f"Nyx could not find a project named '{project}'.",
                    plan.operation,
                )
            hits = await self.rag_service.search(query, project_name=resolved)
        elif plan.operation == "search_inbox":
            hits = await self.rag_service.search(query, inbox_only=True)
        else:
            hits = await self.rag_service.search(query)

        if not hits:
            return self._result_from_provider(
                provider_result,
                f"No matching notes found for '{query}'.",
                plan.operation,
            )

        response_text = self._format_hits(query, hits)
        return self._result_from_provider(provider_result, response_text, plan.operation)

    def _planner_context(self, project_names: list[str]) -> dict[str, Any]:
        """Return static planning context for the provider-backed RAG planner."""

        return {
            "module": "rag",
            "existing_projects": project_names,
            "inbox_path": str(self.config.notes.notes_dir / self.config.notes.inbox_file),
            "projects_dir": str(self.config.notes.projects_dir),
        }

    def _build_planner_prompt(self, request_text: str, project_names: list[str]) -> str:
        """Build the provider prompt used to classify one explicit RAG request."""

        project_list = ", ".join(project_names) if project_names else "(none)"
        return (
            "You are Nyx's Phase 8 RAG planner. "
            "Convert the user's request into one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: search_notes, search_project, search_inbox, reject. "
            "Use search_project only with an existing project from this list: "
            f"{project_list}. If the request is not a semantic search request over local notes/projects, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- search_notes: {"query": str}\n'
            '- search_project: {"project": str, "query": str}\n'
            '- search_inbox: {"query": str}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _parse_plan(self, planner_text: str) -> RagPlan:
        """Parse and validate the JSON plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("RAG planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported RAG operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("RAG planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("RAG planner 'rationale' must be a string when present.")

        return RagPlan(operation=operation, arguments=arguments, rationale=rationale)

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
            raise ValueError("RAG planner did not return a JSON object.")
        return object_match.group(0).strip()

    def _format_hits(self, query: str, hits: list[RagSearchHit]) -> str:
        """Render semantic-search hits into a concise plain-text response."""

        lines = [f"Top matches for '{query}':"]
        for index, hit in enumerate(hits, start=1):
            project = hit.metadata.get("project", "unknown")
            file_name = hit.metadata.get("file_name", "unknown")
            source_path = hit.metadata.get("source_path", "")
            snippet = " ".join(hit.document.split())
            if len(snippet) > 220:
                snippet = snippet[:217] + "..."
            lines.append(f"{index}. [{project}] {file_name} — {snippet}")
            if source_path:
                lines.append(f"   source: {source_path}")
        return "\n".join(lines)

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from a planner payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"RAG action is missing string argument '{key}'.")
        return value.strip()

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
    ) -> RagResult:
        """Build a RAG result while preserving provider metadata."""

        return RagResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used,
            operation=operation,
        )
