"""Persistent memory module for Nyx.

Phase 10 adds file-backed persistent memory with a lightweight proposal flow.
Global memory lives in ``~/.config/nyx/memory.md`` and per-project memory lives
in ``~/notes/projects/<project>/context.md``. Model output proposes candidate
memory updates, and the user can explicitly accept or skip those proposals.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
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

_REMEMBER_PATTERNS = (
    re.compile(r"^\s*remember\b", re.IGNORECASE),
    re.compile(r"\bremember this\b", re.IGNORECASE),
    re.compile(r"\bstore in memory\b", re.IGNORECASE),
    re.compile(r"\badd to memory\b", re.IGNORECASE),
)
_SHOW_GLOBAL_PATTERNS = (
    re.compile(r"\bshow\b.+\bglobal memory\b", re.IGNORECASE),
    re.compile(r"\bshow\b.+\bmemory\b", re.IGNORECASE),
    re.compile(r"\bwhat do you remember\b", re.IGNORECASE),
)
_SHOW_PROJECT_PATTERN = re.compile(
    r"\bshow\b.+\b(?:project )?(?:memory|context)\b.+\bfor\s+(?P<project>[\w-]+)\b",
    re.IGNORECASE,
)
_LIST_PROPOSALS_PATTERNS = (
    re.compile(r"\blist\b.+\bmemory proposals\b", re.IGNORECASE),
    re.compile(r"\bshow\b.+\bmemory proposals\b", re.IGNORECASE),
    re.compile(r"\bpending\b.+\bmemory proposals\b", re.IGNORECASE),
)
_APPLY_PROPOSAL_PATTERN = re.compile(
    r"\b(?:apply|accept)\b.+\bmemory proposal\b(?:\s+(?P<proposal>[a-f0-9]{8}))?",
    re.IGNORECASE,
)
_SKIP_PROPOSAL_PATTERN = re.compile(
    r"\b(?:skip|reject|discard)\b.+\bmemory proposal\b(?:\s+(?P<proposal>[a-f0-9]{8}))?",
    re.IGNORECASE,
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {"propose_global", "propose_project", "reject"}


@dataclass(slots=True)
class MemoryProposal:
    """One persisted pending memory proposal.

    Attributes:
        proposal_id: Stable short identifier shown to the user.
        created_at: ISO timestamp recorded when the proposal was generated.
        target: Memory target type, either ``global`` or ``project``.
        project_name: Project target when ``target == "project"``.
        content: Proposed memory line to append after acceptance.
        status: Proposal state, one of ``pending``, ``applied``, or ``skipped``.
        source_request: Original user request that produced the proposal.
    """

    proposal_id: str
    created_at: str
    target: str
    project_name: str | None
    content: str
    status: str
    source_request: str


@dataclass(slots=True)
class MemoryPlan:
    """Validated provider-produced memory action plan."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class MemoryResult:
    """Structured result returned by the Phase 10 memory module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class MemoryModule:
    """Manage global and per-project persistent memory proposals and storage."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit configuration and provider dependencies."""

        self.config = config
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.memory")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious persistent-memory request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(
            pattern.search(normalized)
            for pattern in (
                *_REMEMBER_PATTERNS,
                *_SHOW_GLOBAL_PATTERNS,
                *_LIST_PROPOSALS_PATTERNS,
                _SHOW_PROJECT_PATTERN,
                _APPLY_PROPOSAL_PATTERN,
                _SKIP_PROPOSAL_PATTERN,
            )
        )

    async def handle(self, request_text: str, model_override: str | None = None) -> MemoryResult:
        """Handle one persistent-memory request."""

        await self._ensure_layout()

        direct_result = await self._handle_direct_command(request_text)
        if direct_result is not None:
            return direct_result

        project_names = await self._list_project_names()
        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, project_names),
            context=self._planner_context(project_names),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Memory planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        content = self._require_string_argument(plan.arguments, "content")
        if plan.operation == "propose_project":
            project_name = self._require_string_argument(plan.arguments, "project")
            if not await self._project_exists(project_name):
                return self._result_from_provider(
                    provider_result,
                    f"Nyx could not find a project named '{project_name}', so no memory proposal was created.",
                    plan.operation,
                )
            proposal = await self._create_proposal(
                target="project",
                project_name=project_name,
                content=content,
                source_request=request_text,
            )
            target_description = f"project memory for {proposal.project_name}"
        else:
            proposal = await self._create_proposal(
                target="global",
                project_name=None,
                content=content,
                source_request=request_text,
            )
            target_description = "global memory"

        response_text = (
            f"Created memory proposal {proposal.proposal_id} for {target_description}: {proposal.content}\n"
            f"Apply with: apply memory proposal {proposal.proposal_id}\n"
            f"Skip with: skip memory proposal {proposal.proposal_id}"
        )
        return self._result_from_provider(provider_result, response_text, plan.operation)

    async def _handle_direct_command(self, request_text: str) -> MemoryResult | None:
        """Handle direct non-provider memory commands such as show/apply/skip."""

        if any(pattern.search(request_text) for pattern in _LIST_PROPOSALS_PATTERNS):
            return await self._list_pending_proposals()

        apply_match = _APPLY_PROPOSAL_PATTERN.search(request_text)
        if apply_match is not None:
            proposal_id = apply_match.group("proposal")
            return await self._apply_proposal(proposal_id)

        skip_match = _SKIP_PROPOSAL_PATTERN.search(request_text)
        if skip_match is not None:
            proposal_id = skip_match.group("proposal")
            return await self._skip_proposal(proposal_id)

        project_match = _SHOW_PROJECT_PATTERN.search(request_text)
        if project_match is not None:
            return await self._show_project_memory(project_match.group("project"))

        if any(pattern.search(request_text) for pattern in _SHOW_GLOBAL_PATTERNS):
            return await self._show_global_memory()

        return None

    def _planner_context(self, project_names: list[str]) -> dict[str, Any]:
        """Return planner context for provider-backed memory proposals."""

        return {
            "module": "memory",
            "global_memory_path": str(self._global_memory_path()),
            "proposal_store_path": str(self._proposals_path()),
            "existing_projects": project_names,
        }

    def _build_planner_prompt(self, request_text: str, project_names: list[str]) -> str:
        """Build the provider prompt for persistent-memory proposal creation."""

        project_list = ", ".join(project_names) if project_names else "(none)"
        return (
            "You are Nyx's Phase 10 persistent-memory planner. "
            "Convert the user's request into exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: propose_global, propose_project, reject. "
            "Use propose_project only with an existing project from this list: "
            f"{project_list}. Never invent a new project name. "
            "The proposed content should be a concise durable memory statement, not a full transcript. "
            "If the request is not a persistent-memory update, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- propose_global: {"content": str}\n'
            '- propose_project: {"project": str, "content": str}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _parse_plan(self, planner_text: str) -> MemoryPlan:
        """Parse and validate one provider-produced memory plan."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Memory planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported memory operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Memory planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Memory planner 'rationale' must be a string when present.")

        return MemoryPlan(operation=operation, arguments=arguments, rationale=rationale)

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
            raise ValueError("Memory planner did not return a JSON object.")
        return object_match.group(0).strip()

    async def _ensure_layout(self) -> None:
        """Create the memory directory layout and seed files when missing."""

        def _sync_ensure() -> None:
            config_dir = self.config.config_path.parent
            config_dir.mkdir(parents=True, exist_ok=True)
            self.config.notes.notes_dir.mkdir(parents=True, exist_ok=True)
            self.config.notes.projects_dir.mkdir(parents=True, exist_ok=True)
            global_memory = self._global_memory_path()
            if not global_memory.exists():
                global_memory.write_text("", encoding="utf-8")
            proposals = self._proposals_path()
            if not proposals.exists():
                proposals.write_text("[]\n", encoding="utf-8")

        await asyncio.to_thread(_sync_ensure)

    def _global_memory_path(self) -> Path:
        """Return the configured global memory markdown path."""

        return self.config.config_path.parent / "memory.md"

    def _proposals_path(self) -> Path:
        """Return the persistent proposal store path used by Phase 10."""

        return self.config.config_path.parent / "memory_proposals.json"

    async def _create_proposal(
        self,
        *,
        target: str,
        project_name: str | None,
        content: str,
        source_request: str,
    ) -> MemoryProposal:
        """Create and persist one new pending memory proposal."""

        proposals = await self._read_proposals()
        proposal = MemoryProposal(
            proposal_id=uuid.uuid4().hex[:8],
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            target=target,
            project_name=project_name,
            content=content.strip(),
            status="pending",
            source_request=source_request,
        )
        proposals.append(proposal)
        await self._write_proposals(proposals)
        return proposal

    async def _read_proposals(self) -> list[MemoryProposal]:
        """Load the proposal store from disk."""

        def _sync_read() -> list[MemoryProposal]:
            raw = self._proposals_path().read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("Memory proposal store must contain a JSON list.")
            return [MemoryProposal(**item) for item in payload]

        return await asyncio.to_thread(_sync_read)

    async def _write_proposals(self, proposals: list[MemoryProposal]) -> None:
        """Persist the full proposal list back to disk."""

        def _sync_write() -> None:
            payload = [asdict(proposal) for proposal in proposals]
            self._proposals_path().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        await asyncio.to_thread(_sync_write)

    async def _list_pending_proposals(self) -> MemoryResult:
        """Return a formatted list of pending memory proposals."""

        proposals = [proposal for proposal in await self._read_proposals() if proposal.status == "pending"]
        if not proposals:
            return self._local_result("No pending memory proposals.", operation="list_proposals")

        lines = ["Pending memory proposals:"]
        for proposal in proposals:
            target = (
                "global memory"
                if proposal.target == "global"
                else f"project memory for {proposal.project_name}"
            )
            lines.append(f"- {proposal.proposal_id} [{target}] {proposal.content}")
        return self._local_result("\n".join(lines), operation="list_proposals")

    async def _apply_proposal(self, proposal_id: str | None) -> MemoryResult:
        """Apply a pending proposal to its target memory file."""

        proposals = await self._read_proposals()
        proposal = self._resolve_pending_proposal(proposals, proposal_id)
        if proposal is None:
            return self._local_result("No pending memory proposal matched that request.", operation="apply")

        target_path = await self._target_path_for_proposal(proposal)

        def _sync_append() -> None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("a", encoding="utf-8") as memory_file:
                if memory_file.tell() > 0:
                    memory_file.write("\n")
                memory_file.write(f"- {proposal.content}\n")

        await asyncio.to_thread(_sync_append)

        proposal.status = "applied"
        await self._write_proposals(proposals)
        return self._local_result(
            f"Applied memory proposal {proposal.proposal_id} to {target_path}.",
            operation="apply",
        )

    async def _skip_proposal(self, proposal_id: str | None) -> MemoryResult:
        """Mark a pending proposal as skipped without applying it."""

        proposals = await self._read_proposals()
        proposal = self._resolve_pending_proposal(proposals, proposal_id)
        if proposal is None:
            return self._local_result("No pending memory proposal matched that request.", operation="skip")

        proposal.status = "skipped"
        await self._write_proposals(proposals)
        return self._local_result(
            f"Skipped memory proposal {proposal.proposal_id}.",
            operation="skip",
        )

    def _resolve_pending_proposal(
        self,
        proposals: list[MemoryProposal],
        proposal_id: str | None,
    ) -> MemoryProposal | None:
        """Resolve a pending proposal by explicit id or latest pending entry."""

        pending = [proposal for proposal in proposals if proposal.status == "pending"]
        if not pending:
            return None
        if proposal_id is None:
            return pending[-1]
        for proposal in pending:
            if proposal.proposal_id == proposal_id:
                return proposal
        return None

    async def _target_path_for_proposal(self, proposal: MemoryProposal) -> Path:
        """Resolve the target markdown file for a proposal."""

        if proposal.target == "global":
            return self._global_memory_path()
        if proposal.project_name is None:
            raise ValueError("Project memory proposal is missing a project target.")
        project_path = await self._resolve_project_path(proposal.project_name)
        if project_path is None:
            raise ValueError(f"Unknown project '{proposal.project_name}'.")
        return project_path / "context.md"

    async def _show_global_memory(self) -> MemoryResult:
        """Return the current global memory file contents."""

        content = await asyncio.to_thread(self._global_memory_path().read_text, encoding="utf-8")
        if not content.strip():
            return self._local_result("Global memory is empty.", operation="show_global")
        return self._local_result(content.strip(), operation="show_global")

    async def _show_project_memory(self, project_name: str) -> MemoryResult:
        """Return the current per-project context contents."""

        project_path = await self._resolve_project_path(project_name)
        if project_path is None:
            return self._local_result(
                f"Nyx could not find a project named '{project_name}'.",
                operation="show_project",
            )
        context_path = project_path / "context.md"
        if not context_path.exists():
            return self._local_result(
                f"Project memory for {project_path.name} is empty.",
                operation="show_project",
            )
        content = await asyncio.to_thread(context_path.read_text, encoding="utf-8")
        if not content.strip():
            return self._local_result(
                f"Project memory for {project_path.name} is empty.",
                operation="show_project",
            )
        return self._local_result(content.strip(), operation="show_project")

    async def _list_project_names(self) -> list[str]:
        """Return existing project directory names under the configured notes tree."""

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
        """Return whether the provided project directory exists."""

        return await self._resolve_project_path(project_name) is not None

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

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from a planner payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Memory action is missing string argument '{key}'.")
        return value.strip()

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
    ) -> MemoryResult:
        """Build a memory result while preserving provider metadata."""

        return MemoryResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.degraded,
            operation=operation,
        )

    def _local_result(self, response_text: str, operation: str) -> MemoryResult:
        """Build a local memory result for non-provider commands."""

        return MemoryResult(
            response_text=response_text,
            used_model="local",
            model_name=None,
            token_count=None,
            degraded=False,
            operation=operation,
        )
