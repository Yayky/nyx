"""Cross-device sync services for Nyx.

Phase 21 combines Git automation for portable notes and memory with Syncthing
support for the local RAG index. This service keeps the implementation small:
it automates Git operations for the notes repository, mirrors global memory
into the notes tree for versioned transport, inspects the local Syncthing
configuration, and generates a folder snippet for the Nyx RAG index when the
user wants help configuring Syncthing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import shutil
from typing import Iterable
import xml.etree.ElementTree as ET

from nyx.config import NyxConfig


class SyncError(RuntimeError):
    """Raised when cross-device sync cannot complete successfully."""


@dataclass(slots=True)
class GitSyncStatus:
    """One snapshot of the configured notes Git repository state."""

    repo_path: Path
    is_repo: bool
    branch: str | None
    remote: str | None
    upstream: str | None
    dirty: bool
    pending_changes: list[str]
    memory_source_exists: bool
    memory_mirror_exists: bool
    ahead_count: int | None
    behind_count: int | None


@dataclass(slots=True)
class GitSyncRun:
    """Structured summary of one completed Git sync run."""

    repo_path: Path
    mirrored_memory: bool
    committed: bool
    pulled: bool
    pushed: bool
    commit_message: str | None
    branch: str | None
    remote: str | None


@dataclass(slots=True)
class SyncthingStatus:
    """One snapshot of the local Syncthing configuration relevant to Nyx."""

    config_path: Path
    config_exists: bool
    folder_id: str
    folder_path: Path
    folder_configured: bool
    device_count: int
    snippet_path: Path


@dataclass(slots=True)
class CommandResult:
    """Captured output from one subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str


class CrossDeviceSyncService:
    """Implement the storage-level mechanics behind Phase 21 sync actions."""

    def __init__(self, config: NyxConfig, logger: logging.Logger | None = None) -> None:
        """Store explicit config and logger dependencies."""

        self.config = config
        self.logger = logger or logging.getLogger("nyx.sync")

    async def git_status(self) -> GitSyncStatus:
        """Return the current Git status for the configured notes repository."""

        repo_path = self.config.sync.notes_repo_path
        memory_source = self._global_memory_path()
        memory_mirror = self.config.sync.memory_mirror_path
        if not repo_path.exists():
            return GitSyncStatus(
                repo_path=repo_path,
                is_repo=False,
                branch=None,
                remote=None,
                upstream=None,
                dirty=False,
                pending_changes=[],
                memory_source_exists=memory_source.exists(),
                memory_mirror_exists=memory_mirror.exists(),
                ahead_count=None,
                behind_count=None,
            )

        repo_check = await self._run_command(
            "git",
            "rev-parse",
            "--show-toplevel",
            cwd=repo_path,
            check=False,
        )
        if repo_check.returncode != 0:
            return GitSyncStatus(
                repo_path=repo_path,
                is_repo=False,
                branch=None,
                remote=None,
                upstream=None,
                dirty=False,
                pending_changes=[],
                memory_source_exists=memory_source.exists(),
                memory_mirror_exists=memory_mirror.exists(),
                ahead_count=None,
                behind_count=None,
            )

        branch = (await self._run_command("git", "branch", "--show-current", cwd=repo_path)).stdout.strip()
        remotes_output = (await self._run_command("git", "remote", cwd=repo_path)).stdout
        remotes = [line.strip() for line in remotes_output.splitlines() if line.strip()]
        remote = remotes[0] if remotes else None
        status_output = (await self._run_command("git", "status", "--short", cwd=repo_path)).stdout
        pending_changes = [line.rstrip() for line in status_output.splitlines() if line.strip()]
        upstream_result = await self._run_command(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            cwd=repo_path,
            check=False,
        )
        upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None
        ahead_count = None
        behind_count = None
        if upstream is not None:
            counts_result = await self._run_command(
                "git",
                "rev-list",
                "--left-right",
                "--count",
                f"{upstream}...HEAD",
                cwd=repo_path,
            )
            behind_raw, ahead_raw = counts_result.stdout.strip().split()
            ahead_count = int(ahead_raw)
            behind_count = int(behind_raw)

        return GitSyncStatus(
            repo_path=repo_path,
            is_repo=True,
            branch=branch or None,
            remote=remote,
            upstream=upstream,
            dirty=bool(pending_changes),
            pending_changes=pending_changes,
            memory_source_exists=memory_source.exists(),
            memory_mirror_exists=memory_mirror.exists(),
            ahead_count=ahead_count,
            behind_count=behind_count,
        )

    async def sync_notes_and_memory(self) -> GitSyncRun:
        """Mirror memory into notes, then commit/pull/push the notes repo."""

        status = await self.git_status()
        if not status.is_repo:
            raise SyncError(
                f"{status.repo_path} is not a git repository. Initialize Git in your notes directory before syncing."
            )

        mirrored_memory = await self._mirror_global_memory()
        repo_path = status.repo_path
        await self._run_command("git", "add", "-A", cwd=repo_path)

        diff_result = await self._run_command("git", "diff", "--cached", "--quiet", cwd=repo_path, check=False)
        committed = diff_result.returncode != 0
        commit_message: str | None = None
        if committed:
            commit_message = f"nyx sync {datetime.now().astimezone().isoformat(timespec='seconds')}"
            await self._run_command("git", "commit", "-m", commit_message, cwd=repo_path)

        pulled = False
        pushed = False
        branch = status.branch
        remote = status.remote
        upstream = status.upstream

        if upstream is not None:
            await self._run_command("git", "pull", "--rebase", "--autostash", cwd=repo_path)
            pulled = True
            await self._run_command("git", "push", cwd=repo_path)
            pushed = True
        elif remote is not None and branch is not None:
            await self._run_command("git", "push", "-u", remote, branch, cwd=repo_path)
            pushed = True

        return GitSyncRun(
            repo_path=repo_path,
            mirrored_memory=mirrored_memory,
            committed=committed,
            pulled=pulled,
            pushed=pushed,
            commit_message=commit_message,
            branch=branch,
            remote=remote,
        )

    async def syncthing_status(self) -> SyncthingStatus:
        """Inspect the local Syncthing configuration for the Nyx RAG folder."""

        config_path = self._resolve_syncthing_config_path()
        folder_path = self.config.rag.db_path
        if not config_path.exists():
            return SyncthingStatus(
                config_path=config_path,
                config_exists=False,
                folder_id=self.config.sync.syncthing_folder_id,
                folder_path=folder_path,
                folder_configured=False,
                device_count=0,
                snippet_path=self.config.sync.syncthing_snippet_path,
            )

        root = await asyncio.to_thread(self._parse_xml_root, config_path)
        folder = self._find_syncthing_folder(root, self.config.sync.syncthing_folder_id, folder_path)
        if folder is None:
            return SyncthingStatus(
                config_path=config_path,
                config_exists=True,
                folder_id=self.config.sync.syncthing_folder_id,
                folder_path=folder_path,
                folder_configured=False,
                device_count=0,
                snippet_path=self.config.sync.syncthing_snippet_path,
            )

        devices = folder.findall("device")
        return SyncthingStatus(
            config_path=config_path,
            config_exists=True,
            folder_id=folder.get("id", self.config.sync.syncthing_folder_id),
            folder_path=Path(folder.get("path", str(folder_path))).expanduser(),
            folder_configured=True,
            device_count=len(devices),
            snippet_path=self.config.sync.syncthing_snippet_path,
        )

    async def prepare_syncthing_snippet(self) -> SyncthingStatus:
        """Write a Syncthing folder snippet for the Nyx RAG index and return status."""

        status = await self.syncthing_status()
        snippet_path = self.config.sync.syncthing_snippet_path

        def _sync_write() -> None:
            snippet_path.parent.mkdir(parents=True, exist_ok=True)
            snippet_path.write_text(self._render_syncthing_folder_snippet(status), encoding="utf-8")

        await asyncio.to_thread(_sync_write)
        return await self.syncthing_status()

    def _global_memory_path(self) -> Path:
        """Return the live global-memory path used by the memory module."""

        return self.config.config_path.parent / "memory.md"

    async def _mirror_global_memory(self) -> bool:
        """Copy the live global-memory file into the notes sync tree when present."""

        source = self._global_memory_path()
        destination = self.config.sync.memory_mirror_path
        if not source.exists():
            return False

        source_text = await asyncio.to_thread(source.read_text, encoding="utf-8")
        current_destination = ""
        if destination.exists():
            current_destination = await asyncio.to_thread(destination.read_text, encoding="utf-8")
        if current_destination == source_text:
            return False

        def _sync_copy() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

        await asyncio.to_thread(_sync_copy)
        return True

    def _resolve_syncthing_config_path(self) -> Path:
        """Resolve the best Syncthing config path, including legacy fallbacks."""

        configured = self.config.sync.syncthing_config_path
        candidates = [
            configured,
            Path("~/.local/state/syncthing/config.xml").expanduser(),
            Path("~/.config/syncthing/config.xml").expanduser(),
        ]
        seen: set[Path] = set()
        for candidate in candidates:
            expanded = candidate.expanduser()
            if expanded in seen:
                continue
            seen.add(expanded)
            if expanded.exists():
                return expanded
        return configured

    def _parse_xml_root(self, config_path: Path) -> ET.Element:
        """Parse one Syncthing XML config file and return its root element."""

        return ET.parse(config_path).getroot()

    def _find_syncthing_folder(
        self,
        root: ET.Element,
        folder_id: str,
        folder_path: Path,
    ) -> ET.Element | None:
        """Find the configured Nyx RAG folder by ID or by matching path."""

        normalized_path = str(folder_path.expanduser())
        for folder in root.findall(".//folder"):
            current_id = folder.get("id")
            current_path = folder.get("path")
            if current_id == folder_id:
                return folder
            if current_path and str(Path(current_path).expanduser()) == normalized_path:
                return folder
        return None

    def _render_syncthing_folder_snippet(self, status: SyncthingStatus) -> str:
        """Render an XML snippet the user can merge into Syncthing config."""

        folder_path = str(status.folder_path.expanduser())
        folder_id = status.folder_id
        return (
            f'<folder id="{folder_id}" label="Nyx RAG" path="{folder_path}" type="sendreceive">\n'
            "  <!-- Add one <device id=\"...\"/> element per device that should share the Nyx RAG index. -->\n"
            "  <!-- Example:\n"
            "       <device id=\"YOUR-LAPTOP-DEVICE-ID\"/>\n"
            "       <device id=\"YOUR-DESKTOP-DEVICE-ID\"/>\n"
            "  -->\n"
            "</folder>\n"
        )

    async def _run_command(
        self,
        *command: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> CommandResult:
        """Run one subprocess command asynchronously and capture its text output."""

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd is not None else None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_data, stderr_data = await process.communicate()
        result = CommandResult(
            returncode=process.returncode,
            stdout=stdout_data.decode("utf-8", errors="replace"),
            stderr=stderr_data.decode("utf-8", errors="replace"),
        )
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise SyncError(
                f"Command {' '.join(command)} failed with code {result.returncode}: {message}"
            )
        return result
