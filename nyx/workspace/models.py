"""Dataclasses for persisted Nyx workspace projects and threads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_WORKSPACE_DB_PATH = Path("~/.local/state/nyx/workspace.db").expanduser()


@dataclass(slots=True)
class WorkspaceProjectRecord:
    """One tracked Git repository persisted in the workspace database."""

    project_id: str
    name: str
    repo_path: str
    linked_note_project: str | None
    default_provider: str | None
    default_mode: str
    default_access: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class WorkspaceThreadRecord:
    """One project-scoped workspace thread persisted in the workspace database."""

    thread_id: str
    project_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    provider_name: str | None
    mode: str
    access_mode: str
    status: str
    branch_name: str | None
    worktree_path: str | None
    archived: bool
    pr_url: str | None
    summary: str | None


@dataclass(slots=True)
class WorkspaceRepoStatus:
    """Current Git metadata for one tracked workspace repository."""

    repo_root: str
    branch_name: str | None
    dirty: bool
    head_summary: str | None = None


@dataclass(slots=True)
class WorkspaceProjectSummary:
    """Workspace project plus live repo status used by the GTK shell."""

    project: WorkspaceProjectRecord
    repo_status: WorkspaceRepoStatus | None
