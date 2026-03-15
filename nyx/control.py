"""Daemon-side overlay control IPC for Nyx."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
import sys
from typing import Any

CONTROL_SOCKET_PATH = Path("~/.local/state/nyx/control.sock").expanduser()


class NyxControlError(RuntimeError):
    """Raised when daemon-control commands cannot be delivered."""


class OverlayControlService:
    """Expose summon/dismiss controls for the overlay through a Unix socket."""

    def __init__(
        self,
        *,
        reload_callback=None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the control service with no active server or UI process."""

        self.logger = logger or logging.getLogger("nyx.control")
        self.socket_path = CONTROL_SOCKET_PATH
        self.reload_callback = reload_callback
        self._server: asyncio.AbstractServer | None = None
        self._launcher_process: asyncio.subprocess.Process | None = None
        self._launcher_watch_task: asyncio.Task[None] | None = None
        self._process_lock = asyncio.Lock()
        self._expected_exit_pid: int | None = None

    async def start(self) -> None:
        """Start listening for local control commands on the Unix socket."""

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self.socket_path))
        self.logger.debug("Nyx control socket listening at %s", self.socket_path)

    async def stop(self) -> None:
        """Stop the control server and any managed overlay child process."""

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.hide_ui()
        watch_task = self._launcher_watch_task
        if watch_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await watch_task
        if self.socket_path.exists():
            self.socket_path.unlink()

    async def toggle_ui(self) -> bool:
        """Toggle the managed launcher child process on or off."""

        if self.is_ui_visible():
            await self.hide_ui()
            return False
        await self.show_ui()
        return True

    async def show_ui(self) -> None:
        """Launch the GTK overlay process when it is not already visible."""

        async with self._process_lock:
            if self.is_ui_visible():
                return
            if self._launcher_process is not None and self._launcher_process.returncode is not None:
                self._launcher_process = None
            self._expected_exit_pid = None
            self._launcher_process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "nyx",
                "--launcher",
            )
            self._launcher_watch_task = asyncio.create_task(self._watch_launcher(self._launcher_process))
            self.logger.info("Started managed Nyx launcher process pid=%s", self._launcher_process.pid)

    async def hide_ui(self) -> None:
        """Terminate the managed launcher process when it is running."""

        watch_task: asyncio.Task[None] | None = None
        async with self._process_lock:
            process = self._launcher_process
            if process is None:
                return
            if process.returncode is not None:
                self._launcher_process = None
                return
            self._expected_exit_pid = process.pid
            process.terminate()
            watch_task = self._launcher_watch_task

        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        finally:
            if watch_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await watch_task

    def is_ui_visible(self) -> bool:
        """Return whether the managed overlay child process is still alive."""

        return self._launcher_process is not None and self._launcher_process.returncode is None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one local JSON control request."""

        try:
            raw_line = await reader.readline()
            payload = json.loads(raw_line.decode("utf-8") or "{}")
            command = str(payload.get("command", "")).strip()
            if command == "toggle":
                visible = await self.toggle_ui()
            elif command == "show":
                await self.show_ui()
                visible = True
            elif command == "hide":
                await self.hide_ui()
                visible = False
            elif command == "status":
                visible = self.is_ui_visible()
            elif command == "reload_config":
                if self.reload_callback is None:
                    raise NyxControlError("Nyx daemon reload callback is not available.")
                await self.reload_callback()
                visible = self.is_ui_visible()
            else:
                raise NyxControlError(f"Unsupported control command: {command or 'empty'}")
            response = {"ok": True, "visible": visible}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        writer.write(json.dumps(response).encode("utf-8") + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _watch_launcher(self, process: asyncio.subprocess.Process) -> None:
        """Watch the managed launcher child process until it exits."""

        returncode = await process.wait()
        async with self._process_lock:
            expected = self._expected_exit_pid == process.pid
            if self._launcher_process is process:
                self._launcher_process = None
            if self._launcher_watch_task is not None and self._launcher_watch_task is asyncio.current_task():
                self._launcher_watch_task = None
            if expected:
                self._expected_exit_pid = None

        if returncode == 0:
            self.logger.info("Managed Nyx launcher exited cleanly.")
        elif expected:
            self.logger.info("Managed Nyx launcher terminated with code %s.", returncode)
        else:
            self.logger.warning("Managed Nyx launcher exited unexpectedly with code %s.", returncode)


async def send_control_command(command: str) -> dict[str, Any]:
    """Send one control command to the running Nyx daemon."""

    if not CONTROL_SOCKET_PATH.exists():
        raise NyxControlError(
            "Nyx daemon control socket is unavailable. Start `nyx --daemon` first, then use `nyx --toggle-ui`."
        )

    reader, writer = await asyncio.open_unix_connection(str(CONTROL_SOCKET_PATH))
    try:
        writer.write(json.dumps({"command": command}).encode("utf-8") + b"\n")
        await writer.drain()
        raw_response = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()

    try:
        response = json.loads(raw_response.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise NyxControlError("Nyx daemon returned an invalid control response.") from exc
    if not response.get("ok", False):
        raise NyxControlError(str(response.get("error", "Nyx daemon control failed.")))
    return response
