"""Tests for workspace project/thread persistence and facade behavior."""

from __future__ import annotations

from pathlib import Path

from nyx.config import load_config
from nyx.workspace.facade import NyxWorkspaceFacade
from nyx.workspace.models import WorkspaceRepoStatus
from nyx.workspace.repo import WorkspaceRepoError
from nyx.workspace.store import WorkspaceStore


class FakeRepoService:
    """Small repo inspector stub used to isolate workspace facade tests."""

    def __init__(self, status_by_path: dict[str, WorkspaceRepoStatus]) -> None:
        self.status_by_path = status_by_path

    def discover(self, requested_path: Path) -> WorkspaceRepoStatus:
        key = str(requested_path.expanduser())
        try:
            return self.status_by_path[key]
        except KeyError as exc:
            raise WorkspaceRepoError(f"Unknown repo path in test: {key}") from exc


def test_workspace_store_round_trips_projects_and_threads(tmp_path: Path) -> None:
    """Projects and threads should persist in the workspace SQLite store."""

    store = WorkspaceStore(tmp_path / "workspace.db")
    project = store.create_project(
        name="Nyx",
        repo_path="/repo/nyx",
        linked_note_project=None,
        default_provider="codex-cli",
        default_mode="chat",
        default_access="supervised",
    )

    created_thread = store.create_thread(
        project_id=project.project_id,
        title="Shell polish",
        provider_name="codex-cli",
        mode="plan",
        access_mode="supervised",
        status="idle",
        branch_name="main",
        worktree_path="/repo/nyx",
    )
    renamed_thread = store.rename_thread(created_thread.thread_id, "Workspace shell polish")
    archived_thread = store.set_thread_archived(created_thread.thread_id, True)

    projects = store.list_projects()
    threads = store.list_threads(project.project_id)

    assert len(projects) == 1
    assert projects[0].name == "Nyx"
    assert len(threads) == 1
    assert renamed_thread is not None
    assert archived_thread is not None
    assert threads[0].title == "Workspace shell polish"
    assert threads[0].archived is True


def test_workspace_store_deleting_project_cascades_threads(tmp_path: Path) -> None:
    """Deleting one project should remove its dependent threads."""

    store = WorkspaceStore(tmp_path / "workspace.db")
    project = store.create_project(
        name="Nyx",
        repo_path="/repo/nyx",
        linked_note_project=None,
        default_provider="codex-cli",
        default_mode="chat",
        default_access="supervised",
    )
    thread = store.create_thread(
        project_id=project.project_id,
        title="Cascade test",
        provider_name="codex-cli",
        mode="chat",
        access_mode="supervised",
    )

    store.delete_project(project.project_id)

    assert store.get_project(project.project_id) is None
    assert store.get_thread(thread.thread_id) is None
    assert store.list_threads(project.project_id) == []


def test_workspace_facade_adds_projects_reuses_existing_and_creates_threads(tmp_path: Path) -> None:
    """The facade should normalize repo roots, avoid duplicate projects, and create threads."""

    config = load_config(tmp_path / "missing.toml")
    store = WorkspaceStore(tmp_path / "workspace.db")
    repo_status = WorkspaceRepoStatus(
        repo_root="/repo/nyx",
        branch_name="main",
        dirty=True,
        head_summary="abc123 Add workspace shell",
    )
    facade = NyxWorkspaceFacade(
        config=config,
        store=store,
        repo_service=FakeRepoService(
            {
                "/tmp/link": repo_status,
                "/repo/nyx": repo_status,
            }
        ),
    )

    project = facade.add_project("/tmp/link")
    duplicate = facade.add_project("/repo/nyx")
    thread = facade.create_thread(project.project_id, "Project-aware thread")
    renamed = facade.rename_thread(thread.thread_id, "Renamed thread")

    summaries = facade.list_projects()
    threads = facade.list_threads(project.project_id)

    assert project.project_id == duplicate.project_id
    assert len(summaries) == 1
    assert summaries[0].repo_status is not None
    assert summaries[0].repo_status.branch_name == "main"
    assert len(threads) == 1
    assert thread.branch_name == "main"
    assert renamed is not None
    assert renamed.title == "Renamed thread"
