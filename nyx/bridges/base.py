"""Bridge abstractions for platform-specific Nyx system integrations.

All OS-specific behavior must flow through ``SystemBridge`` implementations so
core modules remain platform-agnostic and the planned Windows port does not
require rewriting business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable


class BridgeNotImplementedError(RuntimeError):
    """Raised when a bridge operation is unavailable in the current phase."""


class BridgeCommandError(RuntimeError):
    """Raised when a bridge command fails to execute successfully."""


class BridgeConfirmationRequiredError(RuntimeError):
    """Raised when a destructive command requires confirmation before execution."""


class BridgeSecurityError(RuntimeError):
    """Raised when a command violates bridge security rules."""


@dataclass(slots=True)
class WindowInfo:
    """Metadata describing the focused or enumerated window."""

    app_name: str
    window_title: str
    workspace: str | None


@dataclass(slots=True)
class MonitorInfo:
    """Metadata describing one display output exposed by the platform bridge."""

    name: str
    description: str
    width: int
    height: int
    x: int
    y: int
    focused: bool


@dataclass(slots=True)
class AudioRecordingSession:
    """Handle for one in-progress microphone recording.

    Attributes:
        stop_callback: Async callback that finalizes the capture and returns
            ``True`` when the recording completed successfully.
    """

    stop_callback: Callable[[], Awaitable[bool]]

    async def stop(self) -> bool:
        """Finalize the recording and report whether usable audio was produced."""

        return await self.stop_callback()


class SystemBridge(ABC):
    """Abstract platform bridge for all OS-specific system operations."""

    @abstractmethod
    async def get_active_window(self) -> WindowInfo:
        """Return information about the currently focused window."""

    @abstractmethod
    async def move_window_to_workspace(self, window: str, workspace: str) -> bool:
        """Move a named window to the requested workspace."""

    @abstractmethod
    async def list_windows(self) -> list[WindowInfo]:
        """List known windows visible to the platform implementation."""

    @abstractmethod
    async def list_monitors(self) -> list[MonitorInfo]:
        """List known monitor outputs visible to the platform implementation."""

    @abstractmethod
    async def get_focused_monitor(self) -> MonitorInfo | None:
        """Return the currently focused monitor when the platform exposes one."""

    @abstractmethod
    async def screenshot(self, path: str) -> bool:
        """Capture a screenshot to the supplied path."""

    @abstractmethod
    async def start_audio_recording(self, path: str) -> AudioRecordingSession:
        """Begin recording microphone audio to the supplied path."""

    @abstractmethod
    async def run_command(self, command: str, confirm_if_destructive: bool = True) -> str:
        """Execute a system command through the platform bridge."""

    @abstractmethod
    async def list_processes(self) -> list[dict]:
        """Return a simplified process listing."""

    @abstractmethod
    async def kill_process(self, identifier: str) -> bool:
        """Terminate a process identified by name or PID-like identifier."""

    @abstractmethod
    async def set_brightness(self, percent: int) -> bool:
        """Set display brightness to the requested percentage."""

    @abstractmethod
    async def set_volume(self, percent: int) -> bool:
        """Set output volume to the requested percentage."""

    @abstractmethod
    async def get_system_stats(self) -> dict:
        """Return basic system statistics such as CPU and RAM usage."""

    @abstractmethod
    async def notify(self, title: str, body: str) -> None:
        """Send a user-visible notification through the platform implementation."""
