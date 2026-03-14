"""Tests for the Phase 21 cross-device sync module."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.config import load_config
from nyx.modules.cross_device_sync import CrossDeviceSyncModule
from nyx.providers.base import ProviderQueryResult
from nyx.sync import GitSyncRun, GitSyncStatus, SyncthingStatus


@dataclass
class FakeProviderRegistry:
    """Minimal provider registry stub used to test sync planning."""

    result: ProviderQueryResult
    seen_prompt: str | None = None
    seen_context: dict[str, Any] | None = None
    seen_preferred_provider_name: str | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return one deterministic planner result."""

        self.seen_prompt = prompt
        self.seen_context = context
        self.seen_preferred_provider_name = preferred_provider_name
        return self.result


@dataclass
class FakeSyncService:
    """Small sync-service stub returning deterministic status/results."""

    git_status_result: GitSyncStatus
    syncthing_status_result: SyncthingStatus
    sync_run_result: GitSyncRun | None = None

    async def git_status(self) -> GitSyncStatus:
        """Return the configured Git status."""

        return self.git_status_result

    async def syncthing_status(self) -> SyncthingStatus:
        """Return the configured Syncthing status."""

        return self.syncthing_status_result

    async def sync_notes_and_memory(self) -> GitSyncRun:
        """Return the configured sync run result."""

        assert self.sync_run_result is not None
        return self.sync_run_result

    async def prepare_syncthing_snippet(self) -> SyncthingStatus:
        """Return the configured Syncthing status after snippet generation."""

        return self.syncthing_status_result


def _git_status(repo_path: Path) -> GitSyncStatus:
    """Build a deterministic Git status fixture."""

    return GitSyncStatus(
        repo_path=repo_path,
        is_repo=True,
        branch="main",
        remote="origin",
        upstream="origin/main",
        dirty=False,
        pending_changes=[],
        memory_source_exists=True,
        memory_mirror_exists=True,
        ahead_count=0,
        behind_count=0,
    )


def _syncthing_status(config_path: Path, snippet_path: Path, rag_path: Path) -> SyncthingStatus:
    """Build a deterministic Syncthing status fixture."""

    return SyncthingStatus(
        config_path=config_path,
        config_exists=True,
        folder_id="nyx-rag",
        folder_path=rag_path,
        folder_configured=True,
        device_count=2,
        snippet_path=snippet_path,
    )


@pytest.mark.anyio
async def test_cross_device_sync_module_formats_combined_status(tmp_path: Path) -> None:
    """Status requests should render both Git and Syncthing state."""

    config = load_config(tmp_path / "missing.toml")
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"show_status","arguments":{}}',
            fallback_used=False,
        )
    )
    service = FakeSyncService(
        git_status_result=_git_status(tmp_path / "notes"),
        syncthing_status_result=_syncthing_status(
            tmp_path / "config.xml",
            tmp_path / "snippet.xml",
            tmp_path / "rag",
        ),
    )
    module = CrossDeviceSyncModule(
        config=config,
        provider_registry=registry,
        sync_service=service,
        logger=logging.getLogger("test.sync_module"),
    )

    result = await module.handle("show cross-device sync status", model_override="codex-cli")

    assert result.operation == "show_status"
    assert "Git sync status:" in result.response_text
    assert "Syncthing status:" in result.response_text
    assert registry.seen_preferred_provider_name == "codex-cli"


@pytest.mark.anyio
async def test_cross_device_sync_module_runs_git_sync(tmp_path: Path) -> None:
    """Sync requests should execute the Git automation path."""

    config = load_config(tmp_path / "missing.toml")
    repo_path = tmp_path / "notes"
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"sync_git","arguments":{}}',
            fallback_used=False,
        )
    )
    service = FakeSyncService(
        git_status_result=_git_status(repo_path),
        syncthing_status_result=_syncthing_status(
            tmp_path / "config.xml",
            tmp_path / "snippet.xml",
            tmp_path / "rag",
        ),
        sync_run_result=GitSyncRun(
            repo_path=repo_path,
            mirrored_memory=True,
            committed=True,
            pulled=True,
            pushed=True,
            commit_message="nyx sync 2026-03-14T22:00:00+01:00",
            branch="main",
            remote="origin",
        ),
    )
    module = CrossDeviceSyncModule(
        config=config,
        provider_registry=registry,
        sync_service=service,
        logger=logging.getLogger("test.sync_module"),
    )

    result = await module.handle("sync my notes across devices")

    assert "Cross-device Git sync completed" in result.response_text
    assert "pushed the notes repository" in result.response_text


def test_cross_device_sync_matcher_is_conservative() -> None:
    """Only obvious sync requests should route into the Phase 21 module."""

    assert CrossDeviceSyncModule.matches_request("show cross-device sync status") is True
    assert CrossDeviceSyncModule.matches_request("configure Syncthing for the rag index") is True
    assert CrossDeviceSyncModule.matches_request("sync my notes across devices") is True
    assert CrossDeviceSyncModule.matches_request("synchronize this paragraph") is False
