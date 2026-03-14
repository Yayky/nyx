"""Tasks module for Nyx.

Phase 13 adds project-scoped task management backed by ``tasks.md`` files under
``~/notes/projects/<project>/``. Tasks are stored as markdown checkboxes so the
files remain human-editable and automatically participate in the existing RAG
indexing path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any

from nyx.config import NyxConfig
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_TASK_PATTERNS = (
    re.compile(r"\b(task|tasks|todo|to-do)\b", re.IGNORECASE),
    re.compile(r"\bcomplete\b.+\b(task|todo)\b", re.IGNORECASE),
    re.compile(r"\bfinish\b.+\b(task|todo)\b", re.IGNORECASE),
    re.compile(r"\blist\b.+\b(task|tasks|todo)\b", re.IGNORECASE),
    re.compile(r"\bshow\b.+\b(task|tasks|todo)\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_TASK_LINE_PATTERN = re.compile(r"^- \[(?P<done>[ xX])\] (?P<content>.+)$")
_ALLOWED_OPERATIONS = {"add_task", "list_tasks", "complete_task", "reject"}


@dataclass(slots=True)
class TaskPlan:
    """Validated provider-produced task action plan."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class TaskItem:
    """One parsed markdown checkbox task."""

    content: str
    completed: bool


@dataclass(slots=True)
class TasksResult:
    """Structured result returned by the Phase 13 tasks module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class TasksModule:
    """Manage project-scoped tasks stored in markdown checkbox files."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit configuration and provider dependencies."""

        self.config = config
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.tasks")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious tasks request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _TASK_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> TasksResult:
        """Handle one project-task request."""

        await self._ensure_layout()
        project_names = await self._list_project_names()
        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, project_names),
            context=self._planner_context(project_names),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Tasks planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        project_name = self._require_string_argument(plan.arguments, "project")
        resolved_project = await self._resolve_project_name(project_name)
        if resolved_project is None:
            return self._result_from_provider(
                provider_result,
                f"Nyx could not find a project named '{project_name}'.",
                plan.operation,
            )

        if plan.operation == "add_task":
            content = self._require_string_argument(plan.arguments, "content")
            await self._append_task(resolved_project, content)
            return self._result_from_provider(
                provider_result,
                f"Added task to {self._tasks_path(resolved_project)}.",
                plan.operation,
            )

        if plan.operation == "list_tasks":
            include_completed = bool(plan.arguments.get("include_completed", False))
            tasks = await self._read_tasks(resolved_project)
            filtered = [task for task in tasks if include_completed or not task.completed]
            if not filtered:
                status_label = "tasks" if include_completed else "open tasks"
                return self._result_from_provider(
                    provider_result,
                    f"No {status_label} found for {resolved_project}.",
                    plan.operation,
                )
            return self._result_from_provider(
                provider_result,
                self._format_tasks(resolved_project, filtered, include_completed),
                plan.operation,
            )

        task_query = self._require_string_argument(plan.arguments, "task")
        completed = await self._complete_task(resolved_project, task_query)
        if completed is None:
            return self._result_from_provider(
                provider_result,
                f"Nyx could not find an open task matching '{task_query}' in {resolved_project}.",
                plan.operation,
            )
        return self._result_from_provider(
            provider_result,
            f"Completed task in {resolved_project}: {completed}",
            plan.operation,
        )

    def _planner_context(self, project_names: list[str]) -> dict[str, Any]:
        """Return static planning context for the provider-backed task planner."""

        return {
            "module": "tasks",
            "projects_dir": str(self.config.notes.projects_dir),
            "existing_projects": project_names,
            "task_file_name": "tasks.md",
        }

    def _build_planner_prompt(self, request_text: str, project_names: list[str]) -> str:
        """Build the provider prompt for one task request."""

        project_list = ", ".join(project_names) if project_names else "(none)"
        return (
            "You are Nyx's Phase 13 tasks planner. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: add_task, list_tasks, complete_task, reject. "
            "Tasks must target an existing project from this list: "
            f"{project_list}. Never invent a new project name.\n\n"
            "Argument rules:\n"
            '- add_task: {"project": str, "content": str}\n'
            '- list_tasks: {"project": str, "include_completed": bool}\n'
            '- complete_task: {"project": str, "task": str}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _parse_plan(self, planner_text: str) -> TaskPlan:
        """Parse and validate the JSON task plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Tasks planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported tasks operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Tasks planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Tasks planner 'rationale' must be a string when present.")

        return TaskPlan(operation=operation, arguments=arguments, rationale=rationale)

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
            raise ValueError("Tasks planner did not return a JSON object.")
        return object_match.group(0).strip()

    async def _ensure_layout(self) -> None:
        """Ensure the notes and projects directories exist."""

        def _sync_ensure() -> None:
            self.config.notes.notes_dir.mkdir(parents=True, exist_ok=True)
            self.config.notes.projects_dir.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(_sync_ensure)

    async def _list_project_names(self) -> list[str]:
        """Return existing project directory names."""

        if not self.config.notes.projects_dir.exists():
            return []
        return sorted(
            child.name
            for child in self.config.notes.projects_dir.iterdir()
            if child.is_dir()
        )

    async def _resolve_project_name(self, project_name: str) -> str | None:
        """Resolve a case-insensitive project name to its canonical directory name."""

        for candidate in await self._list_project_names():
            if candidate.casefold() == project_name.casefold():
                return candidate
        return None

    def _tasks_path(self, project_name: str) -> Path:
        """Return the tasks file path for one existing project."""

        return self.config.notes.projects_dir / project_name / "tasks.md"

    async def _append_task(self, project_name: str, content: str) -> None:
        """Append one new unchecked task to a project's ``tasks.md`` file."""

        def _sync_append() -> None:
            path = self._tasks_path(project_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = f"- [ ] {content.strip()}\n"
            if path.exists():
                existing = path.read_text(encoding="utf-8")
                prefix = "" if existing.endswith("\n") or not existing else "\n"
                path.write_text(existing + prefix + line, encoding="utf-8")
            else:
                path.write_text(line, encoding="utf-8")

        await asyncio.to_thread(_sync_append)

    async def _read_tasks(self, project_name: str) -> list[TaskItem]:
        """Read and parse checkbox tasks from one project's ``tasks.md`` file."""

        def _sync_read() -> list[TaskItem]:
            path = self._tasks_path(project_name)
            if not path.exists():
                return []
            tasks: list[TaskItem] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                match = _TASK_LINE_PATTERN.match(line.strip())
                if match is None:
                    continue
                tasks.append(
                    TaskItem(
                        content=match.group("content").strip(),
                        completed=match.group("done").strip().lower() == "x",
                    )
                )
            return tasks

        return await asyncio.to_thread(_sync_read)

    async def _complete_task(self, project_name: str, task_query: str) -> str | None:
        """Mark the first matching open task complete and return its content."""

        def _sync_complete() -> str | None:
            path = self._tasks_path(project_name)
            if not path.exists():
                return None

            normalized_query = task_query.casefold()
            lines = path.read_text(encoding="utf-8").splitlines()
            updated_lines: list[str] = []
            completed_task: str | None = None

            for line in lines:
                stripped = line.strip()
                match = _TASK_LINE_PATTERN.match(stripped)
                if match is None:
                    updated_lines.append(line)
                    continue

                content = match.group("content").strip()
                is_completed = match.group("done").strip().lower() == "x"
                if (
                    completed_task is None
                    and not is_completed
                    and normalized_query in content.casefold()
                ):
                    updated_lines.append(f"- [x] {content}")
                    completed_task = content
                else:
                    updated_lines.append(line)

            if completed_task is None:
                return None

            path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
            return completed_task

        return await asyncio.to_thread(_sync_complete)

    def _format_tasks(self, project_name: str, tasks: list[TaskItem], include_completed: bool) -> str:
        """Render project tasks into a plain-text task list."""

        header = (
            f"Tasks for {project_name}:"
            if include_completed
            else f"Open tasks for {project_name}:"
        )
        lines = [header]
        for task in tasks:
            marker = "x" if task.completed else " "
            lines.append(f"- [{marker}] {task.content}")
        return "\n".join(lines)

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from a planner payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Tasks action is missing string argument '{key}'.")
        return value.strip()

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
    ) -> TasksResult:
        """Build a tasks result while preserving provider metadata."""

        return TasksResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.degraded,
            operation=operation,
        )
