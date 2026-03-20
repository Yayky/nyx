"""Facade for workspace project/thread CRUD and repo-aware summaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging

from nyx.config import NyxConfig
from nyx.workspace.models import WorkspaceProjectRecord, WorkspaceProjectSummary, WorkspaceThreadRecord
from nyx.workspace.repo import WorkspaceRepoError, WorkspaceRepoService
from nyx.workspace.store import WorkspaceStore


@dataclass(slots=True)
class WorkspaceSelection:
    """Current selected workspace project and thread records."""

    project: WorkspaceProjectRecord | None
    thread: WorkspaceThreadRecord | None


class NyxWorkspaceFacade:
    """High-level CRUD surface used by the GTK workspace shell."""

    def __init__(
        self,
        *,
        config: NyxConfig,
        store: WorkspaceStore | None = None,
        repo_service: WorkspaceRepoService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the facade with config-backed defaults and storage helpers."""

        self.config = config
        self.store = store or WorkspaceStore(logger=logger)
        self.repo_service = repo_service or WorkspaceRepoService()
        self.logger = logger or logging.getLogger("nyx.workspace.facade")

    def list_projects(self, search: str | None = None) -> list[WorkspaceProjectSummary]:
        """Return tracked projects with live repo status when available."""

        projects = self.store.list_projects(search=search)
        summaries: list[WorkspaceProjectSummary] = []
        for project in projects:
            repo_status = None
            try:
                repo_status = self.repo_service.discover(Path(project.repo_path))
            except WorkspaceRepoError as exc:
                self.logger.debug("Workspace repo status unavailable for %s: %s", project.repo_path, exc)
            summaries.append(WorkspaceProjectSummary(project=project, repo_status=repo_status))
        return summaries

    def add_project(self, repo_path_text: str) -> WorkspaceProjectRecord:
        """Add one Git repo as a tracked workspace project or return the existing record."""

        repo_status = self.repo_service.discover(Path(repo_path_text))
        existing = self.store.get_project_by_repo_path(repo_status.repo_root)
        if existing is not None:
            self.store.touch_project(existing.project_id)
            return self.store.get_project(existing.project_id) or existing
        return self.store.create_project(
            name=Path(repo_status.repo_root).name or repo_status.repo_root,
            repo_path=repo_status.repo_root,
            linked_note_project=None,
            default_provider=self.config.models.default,
            default_mode=self.config.ui.workspace_default_mode,
            default_access=self.config.ui.workspace_default_access,
        )

    def remove_project(self, project_id: str) -> None:
        """Remove one tracked project and its workspace threads."""

        self.store.delete_project(project_id)

    def list_threads(
        self,
        project_id: str,
        *,
        search: str | None = None,
        include_archived: bool = True,
    ) -> list[WorkspaceThreadRecord]:
        """Return threads for one tracked project."""

        return self.store.list_threads(
            project_id,
            search=search,
            include_archived=include_archived,
        )

    def create_thread(self, project_id: str, title: str | None = None) -> WorkspaceThreadRecord:
        """Create one new thread scoped to the selected project."""

        project = self.store.get_project(project_id)
        if project is None:
            raise WorkspaceRepoError("Select a project before creating a thread.")
        repo_status = self.repo_service.discover(Path(project.repo_path))
        thread_title = (title or "").strip() or f"{project.name} thread"
        return self.store.create_thread(
            project_id=project_id,
            title=thread_title,
            provider_name=project.default_provider or self.config.models.default,
            mode=project.default_mode or self.config.ui.workspace_default_mode,
            access_mode=project.default_access or self.config.ui.workspace_default_access,
            status="idle",
            branch_name=repo_status.branch_name,
            worktree_path=repo_status.repo_root,
            summary="Thread created. Agent runs, diffs, and terminal artifacts will attach in later slices.",
        )

    def rename_thread(self, thread_id: str, title: str) -> WorkspaceThreadRecord | None:
        """Rename one existing workspace thread."""

        cleaned = title.strip()
        if not cleaned:
            raise WorkspaceRepoError("Thread title cannot be empty.")
        return self.store.rename_thread(thread_id, cleaned)

    def set_thread_archived(self, thread_id: str, archived: bool) -> WorkspaceThreadRecord | None:
        """Archive or unarchive one workspace thread."""

        return self.store.set_thread_archived(thread_id, archived)

    def delete_thread(self, thread_id: str) -> None:
        """Delete one workspace thread."""

        self.store.delete_thread(thread_id)

    def get_project(self, project_id: str | None) -> WorkspaceProjectRecord | None:
        """Return one selected project when it exists."""

        if not project_id:
            return None
        return self.store.get_project(project_id)

    def get_thread(self, thread_id: str | None) -> WorkspaceThreadRecord | None:
        """Return one selected thread when it exists."""

        if not thread_id:
            return None
        return self.store.get_thread(thread_id)
