"""Tests for Phase 21 cross-device sync services."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nyx.config import load_config
from nyx.sync import CrossDeviceSyncService


async def _run_git(cwd: Path, *command: str) -> None:
    """Run one git command and require success."""

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_data, stderr_data = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(command)} failed: "
            f"{stderr_data.decode('utf-8', errors='replace') or stdout_data.decode('utf-8', errors='replace')}"
        )


@pytest.mark.anyio
async def test_sync_service_reports_non_git_notes_directory(tmp_path: Path) -> None:
    """A plain notes directory should report that Git sync is not ready yet."""

    config = load_config(tmp_path / "missing.toml")
    config.sync.notes_repo_path = tmp_path / "notes"
    config.sync.notes_repo_path.mkdir()
    service = CrossDeviceSyncService(config=config)

    status = await service.git_status()

    assert status.is_repo is False
    assert status.repo_path == config.sync.notes_repo_path


@pytest.mark.anyio
async def test_sync_service_mirrors_memory_and_commits_without_remote(tmp_path: Path) -> None:
    """Git sync should mirror the live memory file and create a local commit."""

    config = load_config(tmp_path / "missing.toml")
    config.config_path = tmp_path / "config.toml"
    config.sync.notes_repo_path = tmp_path / "notes"
    config.sync.memory_mirror_path = config.sync.notes_repo_path / "memory.md"
    config.sync.notes_repo_path.mkdir(parents=True)
    (config.config_path.parent / "memory.md").write_text("- concise answers\n", encoding="utf-8")

    await _run_git(config.sync.notes_repo_path, "git", "init", "-b", "main")
    await _run_git(
        config.sync.notes_repo_path,
        "git",
        "config",
        "user.email",
        "nyx@example.com",
    )
    await _run_git(
        config.sync.notes_repo_path,
        "git",
        "config",
        "user.name",
        "Nyx Test",
    )

    service = CrossDeviceSyncService(config=config)

    run = await service.sync_notes_and_memory()
    status = await service.git_status()

    assert run.mirrored_memory is True
    assert run.committed is True
    assert run.pushed is False
    assert config.sync.memory_mirror_path.read_text(encoding="utf-8") == "- concise answers\n"
    assert status.is_repo is True
    assert status.dirty is False


@pytest.mark.anyio
async def test_sync_service_writes_syncthing_snippet(tmp_path: Path) -> None:
    """Preparing Syncthing config should write a reusable folder snippet."""

    config = load_config(tmp_path / "missing.toml")
    config.rag.db_path = tmp_path / "rag"
    config.sync.syncthing_config_path = tmp_path / "missing-config.xml"
    config.sync.syncthing_snippet_path = tmp_path / "nyx-rag.xml"
    service = CrossDeviceSyncService(config=config)

    status = await service.prepare_syncthing_snippet()

    snippet = config.sync.syncthing_snippet_path.read_text(encoding="utf-8")
    assert status.folder_configured is False
    assert f'id="{config.sync.syncthing_folder_id}"' in snippet
    assert str(config.rag.db_path) in snippet
