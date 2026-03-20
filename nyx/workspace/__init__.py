"""Workspace models, storage, and state helpers for the Nyx desktop workspace."""

from nyx.workspace.facade import NyxWorkspaceFacade, WorkspaceSelection
from nyx.workspace.models import (
    DEFAULT_WORKSPACE_DB_PATH,
    WorkspaceProjectRecord,
    WorkspaceProjectSummary,
    WorkspaceRepoStatus,
    WorkspaceThreadRecord,
)
from nyx.workspace.repo import WorkspaceRepoError, WorkspaceRepoService
from nyx.workspace.state import (
    DEFAULT_WORKSPACE_PROJECTS_PATH,
    DEFAULT_WORKSPACE_STATE_PATH,
    WorkspaceProject,
    WorkspaceProjectRegistry,
    WorkspaceUiState,
    WorkspaceUiStateStore,
)
from nyx.workspace.store import WorkspaceStore

__all__ = [
    "DEFAULT_WORKSPACE_DB_PATH",
    "DEFAULT_WORKSPACE_PROJECTS_PATH",
    "DEFAULT_WORKSPACE_STATE_PATH",
    "NyxWorkspaceFacade",
    "WorkspaceProject",
    "WorkspaceProjectRecord",
    "WorkspaceProjectRegistry",
    "WorkspaceProjectSummary",
    "WorkspaceRepoError",
    "WorkspaceRepoService",
    "WorkspaceRepoStatus",
    "WorkspaceSelection",
    "WorkspaceStore",
    "WorkspaceThreadRecord",
    "WorkspaceUiState",
    "WorkspaceUiStateStore",
]
