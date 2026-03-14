"""Cross-device sync module for Nyx.

Phase 21 combines Git automation for portable notes and memory with Syncthing
support for the local RAG index. This module translates natural-language sync
requests into a small set of explicit actions backed by ``CrossDeviceSyncService``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any

from nyx.config import NyxConfig
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry
from nyx.sync import CrossDeviceSyncService, GitSyncRun, GitSyncStatus, SyncthingStatus

_SYNC_PATTERNS = (
    re.compile(r"\bcross[- ]device\b", re.IGNORECASE),
    re.compile(r"\bsyncthing\b", re.IGNORECASE),
    re.compile(r"\bsync\b.+\b(notes|memory|rag|index|devices?)\b", re.IGNORECASE),
    re.compile(r"\bsync status\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {
    "show_status",
    "sync_git",
    "show_syncthing",
    "prepare_syncthing_config",
    "reject",
}


@dataclass(slots=True)
class CrossDeviceSyncPlan:
    """Validated provider-produced sync action plan."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class CrossDeviceSyncResult:
    """Structured result returned by the Phase 21 sync module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class CrossDeviceSyncModule:
    """Handle explicit cross-device sync requests."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        sync_service: CrossDeviceSyncService,
        logger: logging.Logger | None = None,
    ) -> None:
        """Store config, provider registry, and sync service dependencies."""

        self.config = config
        self.provider_registry = provider_registry
        self.sync_service = sync_service
        self.logger = logger or logging.getLogger("nyx.modules.cross_device_sync")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious sync request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _SYNC_PATTERNS)

    async def handle(
        self,
        request_text: str,
        model_override: str | None = None,
    ) -> CrossDeviceSyncResult:
        """Handle one cross-device sync request."""

        git_status = await self.sync_service.git_status()
        syncthing_status = await self.sync_service.syncthing_status()
        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, git_status, syncthing_status),
            context=self._planner_context(git_status, syncthing_status),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Cross-device sync planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        if plan.operation == "show_status":
            response_text = self._format_combined_status(git_status, syncthing_status)
        elif plan.operation == "sync_git":
            response_text = self._format_git_sync_run(await self.sync_service.sync_notes_and_memory())
        elif plan.operation == "show_syncthing":
            response_text = self._format_syncthing_status(syncthing_status)
        elif plan.operation == "prepare_syncthing_config":
            response_text = self._format_syncthing_snippet_status(
                await self.sync_service.prepare_syncthing_snippet()
            )
        else:
            raise ValueError(f"Unsupported cross-device sync operation: {plan.operation!r}")

        return self._result_from_provider(provider_result, response_text, plan.operation)

    def _planner_context(
        self,
        git_status: GitSyncStatus,
        syncthing_status: SyncthingStatus,
    ) -> dict[str, Any]:
        """Return planning context for the provider-backed sync planner."""

        return {
            "module": "cross_device_sync",
            "notes_repo_path": str(self.config.sync.notes_repo_path),
            "memory_source_path": str(self.config.config_path.parent / "memory.md"),
            "memory_mirror_path": str(self.config.sync.memory_mirror_path),
            "rag_path": str(self.config.rag.db_path),
            "syncthing_config_path": str(syncthing_status.config_path),
            "syncthing_folder_id": syncthing_status.folder_id,
            "notes_repo_is_git": git_status.is_repo,
            "syncthing_config_exists": syncthing_status.config_exists,
            "syncthing_folder_configured": syncthing_status.folder_configured,
        }

    def _build_planner_prompt(
        self,
        request_text: str,
        git_status: GitSyncStatus,
        syncthing_status: SyncthingStatus,
    ) -> str:
        """Build the provider prompt for one sync request."""

        return (
            "You are Nyx's Phase 21 cross-device sync planner. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown or extra prose. Allowed operations: "
            "show_status, sync_git, show_syncthing, prepare_syncthing_config, reject.\n\n"
            "Use show_status for overall sync health or when the user asks about cross-device sync broadly.\n"
            "Use sync_git to sync notes and mirrored global memory through Git automation.\n"
            "Use show_syncthing for questions specifically about the RAG Syncthing setup.\n"
            "Use prepare_syncthing_config when the user asks to configure or prepare Syncthing for the RAG index.\n"
            "Use reject for requests outside cross-device sync.\n\n"
            f"Notes repo path: {git_status.repo_path}\n"
            f"Notes repo is git: {git_status.is_repo}\n"
            f"Memory mirror path: {self.config.sync.memory_mirror_path}\n"
            f"Syncthing config path: {syncthing_status.config_path}\n"
            f"Syncthing folder configured: {syncthing_status.folder_configured}\n"
            f"RAG path: {syncthing_status.folder_path}\n\n"
            f"User request: {request_text}"
        )

    def _parse_plan(self, planner_text: str) -> CrossDeviceSyncPlan:
        """Parse and validate the JSON sync plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Cross-device sync planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported cross-device sync operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Cross-device sync planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Cross-device sync planner 'rationale' must be a string when present.")

        return CrossDeviceSyncPlan(operation=operation, arguments=arguments, rationale=rationale)

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
            raise ValueError("Cross-device sync planner did not return a JSON object.")
        return object_match.group(0).strip()

    def _format_combined_status(
        self,
        git_status: GitSyncStatus,
        syncthing_status: SyncthingStatus,
    ) -> str:
        """Format a readable combined Git and Syncthing status summary."""

        return (
            f"{self._format_git_status(git_status)}\n\n"
            f"{self._format_syncthing_status(syncthing_status)}"
        )

    def _format_git_status(self, status: GitSyncStatus) -> str:
        """Format the current notes Git status for user-facing output."""

        if not status.is_repo:
            return (
                "Git sync status:\n"
                f"- Notes repo path: {status.repo_path}\n"
                "- Notes directory is not a git repository yet.\n"
                f"- Global memory source exists: {'yes' if status.memory_source_exists else 'no'}\n"
                f"- Memory mirror exists: {'yes' if status.memory_mirror_exists else 'no'}"
            )

        lines = [
            "Git sync status:",
            f"- Notes repo path: {status.repo_path}",
            f"- Branch: {status.branch or '(detached)'}",
            f"- Remote: {status.remote or '(none)'}",
            f"- Upstream: {status.upstream or '(none)'}",
            f"- Dirty: {'yes' if status.dirty else 'no'}",
            f"- Global memory source exists: {'yes' if status.memory_source_exists else 'no'}",
            f"- Memory mirror exists: {'yes' if status.memory_mirror_exists else 'no'}",
        ]
        if status.ahead_count is not None and status.behind_count is not None:
            lines.append(f"- Ahead/behind: {status.ahead_count}/{status.behind_count}")
        if status.pending_changes:
            lines.append("- Pending changes:")
            lines.extend(f"  {line}" for line in status.pending_changes[:10])
        return "\n".join(lines)

    def _format_git_sync_run(self, run: GitSyncRun) -> str:
        """Format the result of one completed Git sync operation."""

        actions: list[str] = []
        if run.mirrored_memory:
            actions.append(f"mirrored global memory into {self.config.sync.memory_mirror_path}")
        if run.committed and run.commit_message is not None:
            actions.append(f"created commit '{run.commit_message}'")
        if run.pulled:
            actions.append("pulled remote updates")
        if run.pushed:
            actions.append("pushed the notes repository")
        if not actions:
            actions.append("found nothing new to commit or push")
        return (
            f"Cross-device Git sync completed for {run.repo_path}.\n"
            f"- Branch: {run.branch or '(detached)'}\n"
            f"- Remote: {run.remote or '(none)'}\n"
            f"- Actions: {', '.join(actions)}"
        )

    def _format_syncthing_status(self, status: SyncthingStatus) -> str:
        """Format the current Nyx Syncthing status for user-facing output."""

        lines = [
            "Syncthing status:",
            f"- Config path: {status.config_path}",
            f"- Config exists: {'yes' if status.config_exists else 'no'}",
            f"- Folder id: {status.folder_id}",
            f"- RAG path: {status.folder_path}",
            f"- Folder configured: {'yes' if status.folder_configured else 'no'}",
            f"- Device count: {status.device_count}",
            f"- Snippet path: {status.snippet_path}",
        ]
        return "\n".join(lines)

    def _format_syncthing_snippet_status(self, status: SyncthingStatus) -> str:
        """Format the result of writing one Syncthing folder snippet."""

        return (
            f"Wrote a Syncthing folder snippet for the Nyx RAG index to {status.snippet_path}.\n"
            f"- Folder id: {status.folder_id}\n"
            f"- RAG path: {status.folder_path}\n"
            f"- Existing Syncthing config: {'yes' if status.config_exists else 'no'}\n"
            "- Merge that snippet into your Syncthing folder config and add device IDs for every machine that should share the index."
        )

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
    ) -> CrossDeviceSyncResult:
        """Build a structured module result from one provider outcome."""

        return CrossDeviceSyncResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.degraded,
            operation=operation,
        )

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Require one string planner argument by name."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Cross-device sync planner must provide a non-empty string for {key!r}.")
        return value.strip()
