"""Notes capture and inbox auto-sort module for Nyx.

Phase 7 adds a local markdown notes workflow rooted at ``~/notes``. Captures
land in ``inbox.md`` first, then Nyx can route them into existing project note
files when auto-sort is enabled or when the user explicitly asks to sort the
inbox. New project creation is intentionally deferred because the architecture
requires a user-confirmed proposal before folders are created.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import uuid
from typing import Any

from nyx.config import NyxConfig
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_NOTES_CAPTURE_PATTERNS = (
    re.compile(r"^\s*(note|capture|save|store|log)\b", re.IGNORECASE),
    re.compile(r"\b(add|put|save)\b.+\b(inbox|note|notes)\b", re.IGNORECASE),
    re.compile(r"\bnote down\b", re.IGNORECASE),
)
_SORT_INBOX_PATTERNS = (
    re.compile(r"\bsort\b.+\binbox\b", re.IGNORECASE),
    re.compile(r"\bauto-?sort\b.+\b(inbox|notes)\b", re.IGNORECASE),
    re.compile(r"\broute\b.+\binbox\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ENTRY_HEADER_PATTERN = re.compile(r"^##\s+(?P<timestamp>\S+)\s+\[(?P<entry_id>[^\]]+)\]$", re.MULTILINE)
_ALLOWED_OPERATIONS = {"append_inbox", "route_to_project", "reject"}


@dataclass(slots=True)
class InboxEntry:
    """One persisted inbox record stored in ``inbox.md``.

    Attributes:
        entry_id: Stable identifier used for later inbox auto-sort updates.
        created_at: ISO timestamp recorded at capture time.
        status: Current routing state, either ``inbox`` or ``routed``.
        project: Existing project name when the entry has been routed.
        content: Raw note content captured from the user request.
    """

    entry_id: str
    created_at: str
    status: str
    project: str | None
    content: str


@dataclass(slots=True)
class NotesPlan:
    """Validated provider-produced plan for one note capture or route decision."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class NotesResult:
    """Structured Phase 7 notes-module result returned to the intent router."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class NotesModule:
    """Capture notes to the inbox and auto-route them to existing project notes."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit config and provider dependencies."""

        self.config = config
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.notes")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious Phase 7 notes request."""

        normalized = text.strip()
        if not normalized:
            return False
        if any(pattern.search(normalized) for pattern in _SORT_INBOX_PATTERNS):
            return True
        return any(pattern.search(normalized) for pattern in _NOTES_CAPTURE_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> NotesResult:
        """Capture or sort notes based on one natural-language user request."""

        await self._ensure_layout()

        if any(pattern.search(request_text) for pattern in _SORT_INBOX_PATTERNS):
            return await self.sort_inbox(model_override=model_override)

        project_names = await self._list_project_names()
        planner_prompt = self._build_capture_prompt(request_text, project_names)
        provider_result = await self.provider_registry.query(
            prompt=planner_prompt,
            context=self._planner_context(project_names),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Notes planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        content = self._require_string_argument(plan.arguments, "content")
        entry = await self._append_inbox_entry(content)

        if plan.operation == "append_inbox" or not self.config.notes.auto_sort:
            suffix = "" if self.config.notes.auto_sort else " Auto-sort is disabled."
            return self._result_from_provider(
                provider_result,
                f"Saved note to {self._inbox_path()}.{suffix}",
                plan.operation,
            )

        target_project = self._require_string_argument(plan.arguments, "project")
        if not await self._project_exists(target_project):
            return self._result_from_provider(
                provider_result,
                (
                    f"Saved note to {self._inbox_path()}. "
                    f"Planner suggested unknown project '{target_project}', so Nyx kept it in the inbox."
                ),
                plan.operation,
            )

        project_notes_path = await self._route_entry_to_project(entry, target_project)
        await self._mark_entry_routed(entry.entry_id, target_project)
        return self._result_from_provider(
            provider_result,
            f"Saved note to {self._inbox_path()} and routed it to {project_notes_path}.",
            plan.operation,
        )

    async def sort_inbox(self, model_override: str | None = None) -> NotesResult:
        """Route unrouted inbox entries into existing project notes when possible."""

        await self._ensure_layout()
        entries = await self._read_inbox_entries()
        project_names = await self._list_project_names()
        routed_count = 0

        first_provider_result: ProviderQueryResult | None = None
        degraded = False
        for entry in entries:
            if entry.status != "inbox":
                continue

            provider_result = await self.provider_registry.query(
                prompt=self._build_sort_prompt(entry, project_names),
                context=self._planner_context(project_names),
                preferred_provider_name=model_override,
            )
            if first_provider_result is None:
                first_provider_result = provider_result
            degraded = degraded or provider_result.fallback_used

            plan = self._parse_plan(provider_result.text)
            if plan.operation != "route_to_project":
                continue

            target_project = self._require_string_argument(plan.arguments, "project")
            if not await self._project_exists(target_project):
                continue

            await self._route_entry_to_project(entry, target_project)
            await self._mark_entry_routed(entry.entry_id, target_project)
            routed_count += 1

        provider_name = (
            first_provider_result.provider_name if first_provider_result is not None else self.config.models.default
        )
        model_name = first_provider_result.model_name if first_provider_result is not None else None
        token_count = first_provider_result.token_count if first_provider_result is not None else None
        response_text = f"Routed {routed_count} inbox entr{'y' if routed_count == 1 else 'ies'}."
        return NotesResult(
            response_text=response_text,
            used_model=provider_name,
            model_name=model_name,
            token_count=token_count,
            degraded=degraded,
            operation="sort_inbox",
        )

    def _planner_context(self, project_names: list[str]) -> dict[str, Any]:
        """Return context exposed to the planner provider for Phase 7 routing."""

        return {
            "module": "notes",
            "notes_dir": str(self.config.notes.notes_dir),
            "inbox_path": str(self._inbox_path()),
            "projects_dir": str(self.config.notes.projects_dir),
            "existing_projects": project_names,
            "auto_sort": self.config.notes.auto_sort,
        }

    def _build_capture_prompt(self, request_text: str, project_names: list[str]) -> str:
        """Build the planner prompt for one new note capture request."""

        project_list = ", ".join(project_names) if project_names else "(none)"
        return (
            "You are Nyx's Phase 7 notes planner. "
            "Convert the user's request into exactly one JSON object with keys "
            "operation, arguments, and rationale. Do not return markdown. "
            "Allowed operations are append_inbox, route_to_project, and reject. "
            "Use route_to_project only for an existing project from this list: "
            f"{project_list}. Never invent a new project name. "
            "If the request is not a note capture or inbox-routing request, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- append_inbox: {"content": str}\n'
            '- route_to_project: {"project": str, "content": str}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _build_sort_prompt(self, entry: InboxEntry, project_names: list[str]) -> str:
        """Build the planner prompt for routing one existing inbox entry."""

        project_list = ", ".join(project_names) if project_names else "(none)"
        return (
            "You are Nyx's Phase 7 inbox auto-sort planner. "
            "Return JSON only with keys operation, arguments, and rationale. "
            "Allowed operations are route_to_project, append_inbox, and reject. "
            "Use route_to_project only with an existing project from this list: "
            f"{project_list}. If no existing project matches, use append_inbox.\n\n"
            "Argument rules:\n"
            '- route_to_project: {"project": str, "content": str}\n'
            '- append_inbox: {"content": str}\n'
            '- reject: {"reason": str}\n\n'
            f"Inbox entry content: {entry.content}"
        )

    def _parse_plan(self, planner_text: str) -> NotesPlan:
        """Parse and validate one provider-produced notes plan."""

        payload = self._extract_json_object(planner_text)
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Notes planner returned invalid JSON: {exc}") from exc

        if not isinstance(decoded, dict):
            raise ValueError("Notes planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported notes operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Notes planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Notes planner 'rationale' must be a string when present.")

        return NotesPlan(operation=operation, arguments=arguments, rationale=rationale)

    def _extract_json_object(self, text: str) -> str:
        """Extract a JSON object from raw provider text or fenced JSON output."""

        fenced_match = _JSON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        object_match = _JSON_OBJECT_PATTERN.search(text)
        if object_match is None:
            raise ValueError("Notes planner did not return a JSON object.")
        return object_match.group(0).strip()

    async def _ensure_layout(self) -> None:
        """Create the core notes directories and inbox file when missing."""

        def _sync_ensure() -> None:
            self.config.notes.notes_dir.mkdir(parents=True, exist_ok=True)
            self.config.notes.projects_dir.mkdir(parents=True, exist_ok=True)
            inbox_path = self._inbox_path()
            if not inbox_path.exists():
                inbox_path.write_text("", encoding="utf-8")

        await asyncio.to_thread(_sync_ensure)

    def _inbox_path(self) -> Path:
        """Return the fully resolved inbox markdown file path."""

        return self.config.notes.notes_dir / self.config.notes.inbox_file

    async def _list_project_names(self) -> list[str]:
        """Return the existing project directory names under ``projects_dir``."""

        def _sync_list() -> list[str]:
            if not self.config.notes.projects_dir.exists():
                return []
            return sorted(
                child.name
                for child in self.config.notes.projects_dir.iterdir()
                if child.is_dir()
            )

        return await asyncio.to_thread(_sync_list)

    async def _project_exists(self, project_name: str) -> bool:
        """Return whether the given project directory already exists."""

        project_names = await self._list_project_names()
        return any(name.casefold() == project_name.casefold() for name in project_names)

    async def _append_inbox_entry(self, content: str) -> InboxEntry:
        """Append a new structured entry to ``inbox.md`` and return it."""

        entry = InboxEntry(
            entry_id=uuid.uuid4().hex[:8],
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            status="inbox",
            project=None,
            content=content.strip(),
        )

        def _sync_append() -> None:
            inbox_path = self._inbox_path()
            with inbox_path.open("a", encoding="utf-8") as inbox_file:
                if inbox_file.tell() > 0:
                    inbox_file.write("\n")
                inbox_file.write(self._serialize_entry(entry))

        await asyncio.to_thread(_sync_append)
        return entry

    async def _route_entry_to_project(self, entry: InboxEntry, project_name: str) -> Path:
        """Append one inbox entry into an existing project's ``notes.md`` file."""

        project_path = await self._resolve_project_path(project_name)
        if project_path is None:
            raise ValueError(f"Unknown project '{project_name}'.")
        notes_path = project_path / "notes.md"

        def _sync_append() -> None:
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            with notes_path.open("a", encoding="utf-8") as notes_file:
                if notes_file.tell() > 0:
                    notes_file.write("\n")
                notes_file.write(
                    f"## {entry.created_at}\n\n{entry.content}\n"
                )

        await asyncio.to_thread(_sync_append)
        return notes_path

    async def _resolve_project_path(self, project_name: str) -> Path | None:
        """Resolve a case-insensitive project name to its directory path."""

        def _sync_resolve() -> Path | None:
            if not self.config.notes.projects_dir.exists():
                return None
            for child in self.config.notes.projects_dir.iterdir():
                if child.is_dir() and child.name.casefold() == project_name.casefold():
                    return child
            return None

        return await asyncio.to_thread(_sync_resolve)

    async def _mark_entry_routed(self, entry_id: str, project_name: str) -> None:
        """Rewrite ``inbox.md`` to mark one entry as routed to a project."""

        entries = await self._read_inbox_entries()
        updated_entries: list[InboxEntry] = []
        for entry in entries:
            if entry.entry_id == entry_id:
                updated_entries.append(
                    InboxEntry(
                        entry_id=entry.entry_id,
                        created_at=entry.created_at,
                        status="routed",
                        project=project_name,
                        content=entry.content,
                    )
                )
                continue
            updated_entries.append(entry)
        await self._write_inbox_entries(updated_entries)

    async def _read_inbox_entries(self) -> list[InboxEntry]:
        """Parse structured inbox entries from ``inbox.md``."""

        def _sync_read() -> list[InboxEntry]:
            inbox_path = self._inbox_path()
            if not inbox_path.exists():
                return []
            raw_text = inbox_path.read_text(encoding="utf-8")
            if not raw_text.strip():
                return []

            matches = list(_ENTRY_HEADER_PATTERN.finditer(raw_text))
            entries: list[InboxEntry] = []
            for index, match in enumerate(matches):
                start = match.start()
                end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
                block = raw_text[start:end].strip()
                entry = self._parse_entry_block(block)
                entries.append(entry)
            return entries

        return await asyncio.to_thread(_sync_read)

    async def _write_inbox_entries(self, entries: list[InboxEntry]) -> None:
        """Rewrite the inbox file with the provided structured entry list."""

        def _sync_write() -> None:
            inbox_text = "\n\n".join(self._serialize_entry(entry).rstrip() for entry in entries)
            self._inbox_path().write_text(inbox_text + ("\n" if inbox_text else ""), encoding="utf-8")

        await asyncio.to_thread(_sync_write)

    def _parse_entry_block(self, block: str) -> InboxEntry:
        """Parse one serialized inbox block into an ``InboxEntry``."""

        lines = block.splitlines()
        if len(lines) < 5:
            raise ValueError(f"Malformed inbox entry block: {block!r}")

        header_match = _ENTRY_HEADER_PATTERN.match(lines[0])
        if header_match is None:
            raise ValueError(f"Invalid inbox entry header: {lines[0]!r}")

        status_line = lines[1]
        project_line = lines[2]
        if not status_line.startswith("status: "):
            raise ValueError(f"Invalid inbox entry status line: {status_line!r}")
        if not project_line.startswith("project: "):
            raise ValueError(f"Invalid inbox entry project line: {project_line!r}")

        content = "\n".join(lines[4:]).strip()
        project_value = project_line.removeprefix("project: ").strip()
        return InboxEntry(
            entry_id=header_match.group("entry_id"),
            created_at=header_match.group("timestamp"),
            status=status_line.removeprefix("status: ").strip(),
            project=None if project_value == "-" else project_value,
            content=content,
        )

    def _serialize_entry(self, entry: InboxEntry) -> str:
        """Serialize one inbox entry back into its markdown storage format."""

        project = entry.project if entry.project else "-"
        return (
            f"## {entry.created_at} [{entry.entry_id}]\n"
            f"status: {entry.status}\n"
            f"project: {project}\n"
            "\n"
            f"{entry.content.strip()}\n"
        )

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from a planner payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Notes action is missing string argument '{key}'.")
        return value.strip()

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
    ) -> NotesResult:
        """Build a notes result while preserving provider metadata."""

        return NotesResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used,
            operation=operation,
        )
