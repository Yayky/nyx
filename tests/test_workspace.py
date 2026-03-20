"""Tests for the standalone Nyx workspace shell helpers."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nyx.config import load_config
from nyx.ui import entrypoint
from nyx.workspace.state import WorkspaceProject, WorkspaceProjectRegistry, WorkspaceUiState, WorkspaceUiStateStore


def test_workspace_project_registry_round_trips(tmp_path: Path) -> None:
    """Tracked workspace projects should persist through the JSON registry."""

    registry = WorkspaceProjectRegistry(tmp_path / "workspace_projects.json")
    registry.save(
        [
            WorkspaceProject(
                project_id="nyx",
                display_name="Nyx",
                root_path="/repo/nyx",
                repo_detected=True,
                preferred_provider="codex-cli",
            )
        ]
    )

    loaded = registry.load()
    assert len(loaded) == 1
    assert loaded[0].project_id == "nyx"
    assert loaded[0].preferred_provider == "codex-cli"


def test_workspace_ui_state_store_round_trips(tmp_path: Path) -> None:
    """Workspace shell UI state should persist across launches."""

    store = WorkspaceUiStateStore(tmp_path / "workspace_state.json")
    store.save(
        WorkspaceUiState(
            selected_section="database",
            selected_project_id="nyx",
            provider_name="codex-cli",
            mode="plan",
            access_mode="full",
            search_text="rag",
            terminal_visible=False,
            diff_visible=True,
        )
    )

    loaded = store.load()
    assert loaded.selected_section == "database"
    assert loaded.selected_project_id == "nyx"
    assert loaded.provider_name == "codex-cli"
    assert loaded.mode == "plan"
    assert loaded.access_mode == "full"
    assert loaded.search_text == "rag"
    assert loaded.terminal_visible is False
    assert loaded.diff_visible is True


def test_workspace_project_registry_ignores_invalid_json(tmp_path: Path) -> None:
    """Corrupt project registry state should fall back to an empty list."""

    path = tmp_path / "workspace_projects.json"
    path.write_text("{not-json", encoding="utf-8")

    loaded = WorkspaceProjectRegistry(path).load()

    assert loaded == []


def test_workspace_ui_state_store_ignores_invalid_json(tmp_path: Path) -> None:
    """Corrupt workspace state should fall back to defaults."""

    path = tmp_path / "workspace_state.json"
    path.write_text("{not-json", encoding="utf-8")

    loaded = WorkspaceUiStateStore(path).load()

    assert loaded == WorkspaceUiState()


def test_workspace_entrypoint_surfaces_clear_message_for_missing_gi(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing PyGObject bindings should produce an actionable workspace error."""

    def fake_import():
        raise ModuleNotFoundError("No module named 'gi'", name="gi")

    monkeypatch.setattr(entrypoint, "_import_workspace_impl", fake_import)

    with pytest.raises(RuntimeError, match="python-gobject"):
        entrypoint.run_workspace(
            config=load_config(tmp_path / "missing.toml"),
            logger=logging.getLogger("test.workspace"),
        )
