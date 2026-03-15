"""Tests for Nyx daemon overlay control IPC."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nyx import control


@pytest.mark.anyio
async def test_send_control_command_reports_missing_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control commands should fail clearly when the daemon socket is absent."""

    missing_path = Path("/tmp/nyx-missing-socket")
    monkeypatch.setattr(control, "CONTROL_SOCKET_PATH", missing_path)

    with pytest.raises(control.NyxControlError, match="Start `nyx --daemon` first"):
        await control.send_control_command("toggle")


@pytest.mark.anyio
async def test_overlay_control_service_handles_status_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control service should respond over a Unix socket with JSON state."""

    socket_path = tmp_path / "control.sock"
    monkeypatch.setattr(control, "CONTROL_SOCKET_PATH", socket_path)
    service = control.OverlayControlService()
    await service.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(json.dumps({"command": "status"}).encode("utf-8") + b"\n")
        await writer.drain()
        response = json.loads((await reader.readline()).decode("utf-8"))
        writer.close()
        await writer.wait_closed()
    finally:
        await service.stop()

    assert response == {"ok": True, "visible": False}


@pytest.mark.anyio
async def test_overlay_control_service_handles_reload_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control service should invoke the registered config reload callback."""

    socket_path = tmp_path / "control.sock"
    monkeypatch.setattr(control, "CONTROL_SOCKET_PATH", socket_path)
    called = {"value": False}

    async def reload_callback() -> None:
        called["value"] = True

    service = control.OverlayControlService(reload_callback=reload_callback)
    await service.start()
    try:
        response = await control.send_control_command("reload_config")
    finally:
        await service.stop()

    assert response == {"ok": True, "visible": False}
    assert called["value"] is True
