"""Hyprland-backed Linux bridge implementation for Nyx.

This module contains every Linux-specific system interaction used by Nyx during
Phase 3. All Hyprland, shell, screenshot, audio, brightness, process, and
notification behavior stays behind this bridge so higher-level modules remain
platform-agnostic.
"""

from __future__ import annotations

import asyncio
from asyncio.subprocess import PIPE, Process
from collections.abc import Awaitable, Callable
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
from typing import Any

from nyx.bridges.base import (
    AudioRecordingSession,
    BridgeCommandError,
    BridgeConfirmationRequiredError,
    BridgeSecurityError,
    MonitorInfo,
    SystemBridge,
    WindowInfo,
)
from nyx.config import NyxConfig

BLACKLIST_PATH = Path("~/.config/nyx/blacklist.txt").expanduser()
PROTECTED_PATHS = [
    Path("~/.ssh").expanduser(),
    Path("~/.gnupg").expanduser(),
    Path("/etc"),
    Path("/boot"),
    Path("/sys"),
    Path("/proc"),
]
PROTECTED_PATH_ALIASES = ["~/.ssh", "~/.gnupg"]
DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brm\b",
        r"\bmv\b",
        r"\bdd\b",
        r"\bmkfs(?:\.\w+)?\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bpkill\b",
        r"\bkillall\b",
        r"\bkill\s+-9\b",
        r"(^|[^<])>>?",
    )
]

SubprocessFactory = Callable[..., Awaitable[Process]]


class HyprlandBridge(SystemBridge):
    """Linux bridge implementation backed by Hyprland and common Wayland tools."""

    def __init__(
        self,
        config: NyxConfig,
        logger: logging.Logger | None = None,
        subprocess_factory: SubprocessFactory | None = None,
    ) -> None:
        """Initialize the bridge with explicit config and process dependencies.

        Args:
            config: Loaded Nyx configuration used for runtime behavior such as
                YOLO mode and destructive-command confirmation.
            logger: Optional logger for bridge diagnostics.
            subprocess_factory: Optional async subprocess factory used mainly by
                tests to inject fake command execution.
        """

        self.config = config
        self.logger = logger or logging.getLogger("nyx.bridge.hyprland")
        self._subprocess_factory = subprocess_factory or asyncio.create_subprocess_exec
        self._blacklist_patterns = self._load_blacklist_patterns()

    async def get_active_window(self) -> WindowInfo:
        """Return the currently focused Hyprland window.

        The bridge first tries ``hyprctl activewindow -j``. Some Hyprland
        versions occasionally return an empty JSON object, so the implementation
        falls back to plain-text output and then to the focused client entry.
        """

        active_data = await self._hyprctl_json("activewindow")
        if isinstance(active_data, dict) and active_data:
            return self._window_info_from_hyprctl(active_data)

        stdout = await self._hyprctl_text("activewindow")
        if stdout.strip():
            parsed = self._parse_active_window_text(stdout)
            if parsed is not None:
                return parsed

        windows = await self._get_client_entries()
        if windows:
            focused = min(
                windows,
                key=lambda entry: int(entry.get("focusHistoryID", 2**31 - 1)),
            )
            return self._window_info_from_hyprctl(focused)

        return WindowInfo(app_name="", window_title="", workspace=None)

    async def move_window_to_workspace(self, window: str, workspace: str) -> bool:
        """Move a Hyprland client to the target workspace.

        Args:
            window: Window selector, either a Hyprland address or a class/title
                string matched case-insensitively against current clients.
            workspace: Target workspace identifier understood by Hyprland.

        Returns:
            ``True`` when the dispatch succeeds, otherwise ``False``.
        """

        address = await self._resolve_window_address(window)
        if address is None:
            self.logger.warning("Unable to resolve window selector '%s'.", window)
            return False

        result = await self._run_command_exec(
            "hyprctl",
            "dispatch",
            "movetoworkspacesilent",
            f"{workspace},address:{address}",
            check=False,
        )
        return result.returncode == 0

    async def list_windows(self) -> list[WindowInfo]:
        """List mapped Hyprland clients as ``WindowInfo`` objects."""

        entries = await self._get_client_entries()
        return [self._window_info_from_hyprctl(entry) for entry in entries]

    async def list_monitors(self) -> list[MonitorInfo]:
        """List Hyprland monitor outputs as structured monitor metadata."""

        data = await self._hyprctl_json("monitors")
        if not isinstance(data, list):
            return []
        monitors: list[MonitorInfo] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            monitors.append(self._monitor_info_from_hyprctl(entry))
        return monitors

    async def get_focused_monitor(self) -> MonitorInfo | None:
        """Return the currently focused Hyprland monitor when available."""

        monitors = await self.list_monitors()
        for monitor in monitors:
            if monitor.focused:
                return monitor
        return monitors[0] if monitors else None

    async def screenshot(self, path: str) -> bool:
        """Capture a screenshot using ``grim``."""

        output_path = Path(path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = await self._run_command_exec("grim", str(output_path), check=False)
        return result.returncode == 0

    async def start_audio_recording(self, path: str) -> AudioRecordingSession:
        """Begin recording microphone audio to a WAV file using PipeWire."""

        output_path = Path(path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        record_binary = shutil.which("pw-record")
        if record_binary is None:
            raise BridgeCommandError(
                "pw-record was not found. Install PipeWire tools to use live microphone input."
            )

        process = await self._subprocess_factory(
            record_binary,
            "--media-type=Audio",
            "--media-category=Capture",
            "--media-role=Communication",
            "--rate",
            "16000",
            "--channels",
            "1",
            "--format",
            "s16",
            "--container",
            "wav",
            str(output_path),
            stdout=PIPE,
            stderr=PIPE,
        )

        async def _stop_recording() -> bool:
            """Terminate the capture process and validate the recorded output."""

            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

            stdout_bytes, stderr_bytes = await process.communicate()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            if output_path.exists() and output_path.stat().st_size > 44:
                return True

            if process.returncode not in (0, -signal.SIGTERM):
                raise BridgeCommandError(
                    "pw-record failed with exit code "
                    f"{process.returncode}: {stderr or stdout or 'microphone capture failed'}"
                )
            return False

        return AudioRecordingSession(stop_callback=_stop_recording)

    async def run_command(self, command: str, confirm_if_destructive: bool = True) -> str:
        """Execute a shell command after bridge-level safety checks.

        Args:
            command: Shell command string to execute.
            confirm_if_destructive: Whether destructive commands should be
                blocked until a confirmation UX exists.

        Returns:
            The command's stdout, or stderr when stdout is empty.

        Raises:
            BridgeSecurityError: The command matches the blacklist or protected
                path checks.
            BridgeConfirmationRequiredError: The command is considered
                destructive and confirmation is required.
            BridgeCommandError: The command exits unsuccessfully.
        """

        self._enforce_command_safety(command)
        if (
            confirm_if_destructive
            and self.config.system.confirm_destructive
            and not self.config.system.yolo
            and self._is_destructive_command(command)
        ):
            raise BridgeConfirmationRequiredError(
                "Destructive command blocked until confirmation UI is implemented."
            )

        result = await self._run_command_exec("/bin/bash", "-lc", command, check=False)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "command failed"
            raise BridgeCommandError(
                f"Command failed with exit code {result.returncode}: {message}"
            )
        return result.stdout.strip() or result.stderr.strip()

    async def list_processes(self) -> list[dict]:
        """Return a simplified process list using ``ps``."""

        result = await self._run_command_exec("ps", "-eo", "pid=,comm=,args=", check=True)
        processes: list[dict] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(None, 2)
            pid = int(parts[0]) if parts and parts[0].isdigit() else None
            name = parts[1] if len(parts) > 1 else ""
            args = parts[2] if len(parts) > 2 else ""
            processes.append({"pid": pid, "name": name, "command": args})
        return processes

    async def kill_process(self, identifier: str) -> bool:
        """Terminate a process by PID or pattern."""

        if identifier.isdigit():
            try:
                os.kill(int(identifier), signal.SIGTERM)
            except OSError as exc:
                self.logger.warning("Failed to kill pid %s: %s", identifier, exc)
                return False
            return True

        result = await self._run_command_exec("pkill", "-f", identifier, check=False)
        return result.returncode == 0

    async def set_brightness(self, percent: int) -> bool:
        """Set brightness using ``brightnessctl``."""

        clamped = self._validate_percentage(percent, label="brightness")
        result = await self._run_command_exec("brightnessctl", "set", f"{clamped}%", check=False)
        return result.returncode == 0

    async def set_volume(self, percent: int) -> bool:
        """Set volume using ``wpctl`` and clear mute state."""

        clamped = self._validate_percentage(percent, label="volume")
        unmute_result = await self._run_command_exec(
            "wpctl",
            "set-mute",
            "@DEFAULT_AUDIO_SINK@",
            "0",
            check=False,
        )
        volume_result = await self._run_command_exec(
            "wpctl",
            "set-volume",
            "@DEFAULT_AUDIO_SINK@",
            f"{clamped}%",
            check=False,
        )
        return unmute_result.returncode == 0 and volume_result.returncode == 0

    async def get_system_stats(self) -> dict:
        """Return CPU, memory, and disk summary statistics."""

        memory_total = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        memory_available = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES"))
        disk = shutil.disk_usage("/")
        load_1m, load_5m, load_15m = os.getloadavg()
        return {
            "cpu_count": os.cpu_count() or 0,
            "load_average": {
                "1m": load_1m,
                "5m": load_5m,
                "15m": load_15m,
            },
            "memory_total_bytes": memory_total,
            "memory_available_bytes": memory_available,
            "memory_used_bytes": memory_total - memory_available,
            "disk_total_bytes": disk.total,
            "disk_used_bytes": disk.used,
            "disk_free_bytes": disk.free,
        }

    async def notify(self, title: str, body: str) -> None:
        """Send a desktop notification using ``notify-send``."""

        result = await self._run_command_exec("notify-send", title, body, check=False)
        if result.returncode != 0:
            self.logger.warning(
                "notify-send failed with exit code %s: %s",
                result.returncode,
                result.stderr.strip() or result.stdout.strip(),
            )

    async def _get_client_entries(self) -> list[dict[str, Any]]:
        """Fetch the current Hyprland client list as JSON objects."""

        data = await self._hyprctl_json("clients")
        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict)]

    async def _resolve_window_address(self, window: str) -> str | None:
        """Resolve a window selector to a Hyprland client address."""

        if window.startswith("0x"):
            return window

        candidates = await self._get_client_entries()
        selector = window.casefold()

        exact_match = next(
            (
                entry
                for entry in candidates
                if selector
                == str(entry.get("class", "")).casefold()
                or selector == str(entry.get("title", "")).casefold()
            ),
            None,
        )
        if exact_match is not None:
            return str(exact_match.get("address"))

        partial_matches = [
            entry
            for entry in candidates
            if selector in str(entry.get("class", "")).casefold()
            or selector in str(entry.get("title", "")).casefold()
        ]
        if partial_matches:
            return str(partial_matches[0].get("address"))
        return None

    async def _hyprctl_json(self, command: str) -> Any:
        """Run a Hyprland JSON command and decode the response."""

        result = await self._run_command_exec("hyprctl", command, "-j", check=False)
        stdout = result.stdout.strip()
        if result.returncode != 0:
            raise BridgeCommandError(
                f"hyprctl {command} -j failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or stdout}"
            )
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BridgeCommandError(
                f"hyprctl {command} -j returned invalid JSON: {stdout}"
            ) from exc

    async def _hyprctl_text(self, command: str) -> str:
        """Run a Hyprland text command and return its stdout."""

        result = await self._run_command_exec("hyprctl", command, check=False)
        if result.returncode != 0:
            raise BridgeCommandError(
                f"hyprctl {command} failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    def _window_info_from_hyprctl(self, data: dict[str, Any]) -> WindowInfo:
        """Convert a Hyprland window payload into ``WindowInfo``."""

        workspace = data.get("workspace")
        workspace_id: str | None
        if isinstance(workspace, dict):
            raw_id = workspace.get("id")
            workspace_id = str(raw_id) if raw_id is not None else None
        else:
            workspace_id = None

        return WindowInfo(
            app_name=str(data.get("class", "")),
            window_title=str(data.get("title", "")),
            workspace=workspace_id,
        )

    def _monitor_info_from_hyprctl(self, data: dict[str, Any]) -> MonitorInfo:
        """Convert a Hyprland monitor payload into ``MonitorInfo``."""

        return MonitorInfo(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            width=int(data.get("width", 0) or 0),
            height=int(data.get("height", 0) or 0),
            x=int(data.get("x", 0) or 0),
            y=int(data.get("y", 0) or 0),
            focused=bool(data.get("focused", False)),
        )

    def _parse_active_window_text(self, stdout: str) -> WindowInfo | None:
        """Parse plain-text ``hyprctl activewindow`` output."""

        extracted: dict[str, str] = {}
        for line in stdout.splitlines():
            match = re.match(r"^\s*([A-Za-z]+):\s*(.*)$", line)
            if not match:
                continue
            key, value = match.groups()
            extracted[key.casefold()] = value.strip()

        if not extracted:
            return None

        workspace_value = extracted.get("workspace")
        workspace = None
        if workspace_value:
            workspace = workspace_value.split()[0]

        return WindowInfo(
            app_name=extracted.get("class", ""),
            window_title=extracted.get("title", ""),
            workspace=workspace,
        )

    def _load_blacklist_patterns(self) -> list[str]:
        """Load user blacklist patterns from disk if present."""

        if not BLACKLIST_PATH.exists():
            return []

        patterns: list[str] = []
        for line in BLACKLIST_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            patterns.append(stripped)
        return patterns

    def _enforce_command_safety(self, command: str) -> None:
        """Reject commands that violate blacklist or protected-path rules."""

        lower_command = command.casefold()
        for pattern in self._blacklist_patterns:
            if pattern.casefold() in lower_command:
                raise BridgeSecurityError(
                    f"Command blocked by blacklist pattern '{pattern}'."
                )

        for protected_path in PROTECTED_PATHS:
            protected_string = str(protected_path).casefold()
            if protected_string in lower_command:
                raise BridgeSecurityError(
                    f"Command blocked because it targets protected path '{protected_path}'."
                )

        for protected_alias in PROTECTED_PATH_ALIASES:
            if protected_alias.casefold() in lower_command:
                raise BridgeSecurityError(
                    f"Command blocked because it targets protected path '{protected_alias}'."
                )

    def _is_destructive_command(self, command: str) -> bool:
        """Return whether a command appears destructive enough to require confirmation."""

        return any(pattern.search(command) for pattern in DESTRUCTIVE_COMMAND_PATTERNS)

    def _validate_percentage(self, percent: int, label: str) -> int:
        """Validate and clamp a percentage-like integer input."""

        if not 0 <= percent <= 100:
            raise ValueError(f"{label} percent must be between 0 and 100.")
        return int(percent)

    async def _run_command_exec(
        self,
        *command: str,
        check: bool,
    ) -> _ProcessResult:
        """Run a subprocess command asynchronously and collect its output."""

        process = await self._subprocess_factory(*command, stdout=PIPE, stderr=PIPE)
        stdout_data, stderr_data = await process.communicate()
        result = _ProcessResult(
            args=list(command),
            returncode=process.returncode or 0,
            stdout=stdout_data.decode(),
            stderr=stderr_data.decode(),
        )
        if check and result.returncode != 0:
            raise BridgeCommandError(
                f"Command {' '.join(shlex.quote(arg) for arg in command)} failed "
                f"with exit code {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result


class _ProcessResult:
    """Simple subprocess result container used by ``HyprlandBridge`` helpers."""

    def __init__(self, args: list[str], returncode: int, stdout: str, stderr: str) -> None:
        """Store the command result from an asynchronous subprocess call."""

        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
