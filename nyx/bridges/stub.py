"""Phase 1 placeholder bridge implementation.

The stub bridge allows Nyx to boot cleanly before the real Hyprland bridge is
implemented. Any attempted system action fails explicitly so incomplete platform
support is obvious during development.
"""

from __future__ import annotations

import logging

from nyx.bridges.base import (
    AudioRecordingSession,
    BridgeNotImplementedError,
    MonitorInfo,
    SystemBridge,
    WindowInfo,
)


class StubBridge(SystemBridge):
    """Placeholder ``SystemBridge`` used until real platform bridges exist."""

    def __init__(self, platform_name: str, logger: logging.Logger | None = None) -> None:
        """Initialize the stub bridge for diagnostics.

        Args:
            platform_name: Human-readable platform name used in error messages.
            logger: Optional logger; defaults to the ``nyx.bridge.stub`` logger.
        """

        self.platform_name = platform_name
        self.logger = logger or logging.getLogger("nyx.bridge.stub")

    async def get_active_window(self) -> WindowInfo:
        """Raise because active window support is not implemented in Phase 1."""

        raise self._not_implemented("get_active_window")

    async def move_window_to_workspace(self, window: str, workspace: str) -> bool:
        """Raise because window movement is not implemented in Phase 1."""

        raise self._not_implemented("move_window_to_workspace")

    async def list_windows(self) -> list[WindowInfo]:
        """Raise because window enumeration is not implemented in Phase 1."""

        raise self._not_implemented("list_windows")

    async def list_monitors(self) -> list[MonitorInfo]:
        """Raise because monitor enumeration is not implemented in Phase 1."""

        raise self._not_implemented("list_monitors")

    async def get_focused_monitor(self) -> MonitorInfo | None:
        """Raise because focused-monitor lookup is not implemented in Phase 1."""

        raise self._not_implemented("get_focused_monitor")

    async def screenshot(self, path: str) -> bool:
        """Raise because screenshot support is not implemented in Phase 1."""

        raise self._not_implemented("screenshot")

    async def start_audio_recording(self, path: str) -> AudioRecordingSession:
        """Raise because microphone capture is not implemented in Phase 1."""

        raise self._not_implemented("start_audio_recording")

    async def run_command(self, command: str, confirm_if_destructive: bool = True) -> str:
        """Raise because command execution is not implemented in Phase 1."""

        raise self._not_implemented("run_command")

    async def list_processes(self) -> list[dict]:
        """Raise because process inspection is not implemented in Phase 1."""

        raise self._not_implemented("list_processes")

    async def kill_process(self, identifier: str) -> bool:
        """Raise because process termination is not implemented in Phase 1."""

        raise self._not_implemented("kill_process")

    async def set_brightness(self, percent: int) -> bool:
        """Raise because brightness control is not implemented in Phase 1."""

        raise self._not_implemented("set_brightness")

    async def set_volume(self, percent: int) -> bool:
        """Raise because volume control is not implemented in Phase 1."""

        raise self._not_implemented("set_volume")

    async def get_system_stats(self) -> dict:
        """Raise because system stats are not implemented in Phase 1."""

        raise self._not_implemented("get_system_stats")

    async def notify(self, title: str, body: str) -> None:
        """Log a warning because notifications are not implemented in Phase 1."""

        self.logger.warning(
            "Notification requested via StubBridge on %s: %s - %s",
            self.platform_name,
            title,
            body,
        )

    def _not_implemented(self, method_name: str) -> BridgeNotImplementedError:
        """Construct a consistent Phase 1 bridge error with logging context."""

        message = (
            f"{method_name} is not implemented for platform "
            f"{self.platform_name} in Phase 1."
        )
        self.logger.error(message)
        return BridgeNotImplementedError(message)
