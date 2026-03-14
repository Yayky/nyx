"""Tests for the Phase 3 Hyprland bridge implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nyx.bridges.base import (
    BridgeConfirmationRequiredError,
    BridgeSecurityError,
    MonitorInfo,
    WindowInfo,
)
from nyx.bridges.hyprland import HyprlandBridge
from nyx.config import load_config


class FakeProcess:
    """Minimal fake subprocess used to drive bridge tests."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int | None = 0) -> None:
        """Store deterministic subprocess output for a bridge command."""

        self.returncode = returncode
        self._stdout = stdout.encode()
        self._stderr = stderr.encode()
        self.terminated = False

    async def communicate(self) -> tuple[bytes, bytes]:
        """Return the configured stdout and stderr bytes."""

        return self._stdout, self._stderr

    async def wait(self) -> int:
        """Return the configured return code for wait-based callers."""

        return 0 if self.returncode is None else self.returncode

    def terminate(self) -> None:
        """Mark the fake process as terminated."""

        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        """Mark the fake process as killed."""

        self.terminated = True
        self.returncode = -9


class FakeSubprocessFactory:
    """Command-dispatching subprocess factory for Hyprland bridge tests."""

    def __init__(self, responses: dict[tuple[str, ...], FakeProcess]) -> None:
        """Initialize the factory with exact command-to-process mappings."""

        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *command: str, **kwargs: Any) -> FakeProcess:
        """Return the fake subprocess for the requested command."""

        self.calls.append(tuple(command))
        try:
            return self.responses[tuple(command)]
        except KeyError as exc:
            raise AssertionError(f"Unexpected command: {command}") from exc


@pytest.mark.anyio
async def test_get_active_window_falls_back_to_plain_text(tmp_path: Path) -> None:
    """The bridge should parse text output when JSON active window is empty."""

    factory = FakeSubprocessFactory(
        {
            ("hyprctl", "activewindow", "-j"): FakeProcess(stdout="{}"),
            (
                "hyprctl",
                "activewindow",
            ): FakeProcess(
                stdout=(
                    "Window 5594bde90100 -> Example:\n"
                    "\tworkspace: 4 (4)\n"
                    "\tclass: org.kde.dolphin\n"
                    "\ttitle: Example\n"
                )
            ),
        }
    )
    bridge = HyprlandBridge(
        config=load_config(tmp_path / "missing.toml"),
        subprocess_factory=factory,
    )

    window = await bridge.get_active_window()

    assert window == WindowInfo(
        app_name="org.kde.dolphin",
        window_title="Example",
        workspace="4",
    )


@pytest.mark.anyio
async def test_list_windows_parses_hyprland_clients_json(tmp_path: Path) -> None:
    """The bridge should convert Hyprland client payloads to ``WindowInfo``."""

    factory = FakeSubprocessFactory(
        {
            ("hyprctl", "clients", "-j"): FakeProcess(
                stdout=(
                    '[{"address":"0x1","workspace":{"id":2},"class":"kitty","title":"shell"},'
                    '{"address":"0x2","workspace":{"id":4},"class":"brave-browser","title":"docs"}]'
                )
            )
        }
    )
    bridge = HyprlandBridge(
        config=load_config(tmp_path / "missing.toml"),
        subprocess_factory=factory,
    )

    windows = await bridge.list_windows()

    assert windows == [
        WindowInfo(app_name="kitty", window_title="shell", workspace="2"),
        WindowInfo(app_name="brave-browser", window_title="docs", workspace="4"),
    ]


@pytest.mark.anyio
async def test_get_focused_monitor_parses_hyprland_monitors_json(tmp_path: Path) -> None:
    """Focused monitor lookup should parse Hyprland monitor payloads."""

    factory = FakeSubprocessFactory(
        {
            ("hyprctl", "monitors", "-j"): FakeProcess(
                stdout=(
                    '[{"name":"HDMI-A-1","description":"Dell 27","width":2560,"height":1440,'
                    '"x":1920,"y":0,"focused":false},'
                    '{"name":"eDP-2","description":"Laptop Panel","width":1920,"height":1080,'
                    '"x":0,"y":0,"focused":true}]'
                )
            )
        }
    )
    bridge = HyprlandBridge(
        config=load_config(tmp_path / "missing.toml"),
        subprocess_factory=factory,
    )

    monitor = await bridge.get_focused_monitor()

    assert monitor == MonitorInfo(
        name="eDP-2",
        description="Laptop Panel",
        width=1920,
        height=1080,
        x=0,
        y=0,
        focused=True,
    )


@pytest.mark.anyio
async def test_move_window_to_workspace_resolves_title_selector(tmp_path: Path) -> None:
    """Window selectors should resolve to client addresses before dispatching."""

    factory = FakeSubprocessFactory(
        {
            ("hyprctl", "clients", "-j"): FakeProcess(
                stdout=(
                    '[{"address":"0xabc","workspace":{"id":1},"class":"kitty","title":"shell"}]'
                )
            ),
            (
                "hyprctl",
                "dispatch",
                "movetoworkspacesilent",
                "5,address:0xabc",
            ): FakeProcess(stdout="ok"),
        }
    )
    bridge = HyprlandBridge(
        config=load_config(tmp_path / "missing.toml"),
        subprocess_factory=factory,
    )

    moved = await bridge.move_window_to_workspace("shell", "5")

    assert moved is True
    assert (
        "hyprctl",
        "dispatch",
        "movetoworkspacesilent",
        "5,address:0xabc",
    ) in factory.calls


@pytest.mark.anyio
async def test_run_command_blocks_protected_paths(tmp_path: Path) -> None:
    """Protected paths should be blocked before command execution."""

    bridge = HyprlandBridge(config=load_config(tmp_path / "missing.toml"))

    with pytest.raises(BridgeSecurityError, match="/etc"):
        await bridge.run_command("cat /etc/passwd")


@pytest.mark.anyio
async def test_run_command_requires_confirmation_for_destructive_commands(tmp_path: Path) -> None:
    """Destructive shell commands should require confirmation outside YOLO mode."""

    bridge = HyprlandBridge(config=load_config(tmp_path / "missing.toml"))

    with pytest.raises(BridgeConfirmationRequiredError):
        await bridge.run_command("rm -rf ~/tmp")


@pytest.mark.anyio
async def test_run_command_executes_safe_shell_command(tmp_path: Path) -> None:
    """Safe commands should execute through the shell and return stdout."""

    factory = FakeSubprocessFactory(
        {
            ("/bin/bash", "-lc", "printf 'hello'"): FakeProcess(stdout="hello"),
        }
    )
    bridge = HyprlandBridge(
        config=load_config(tmp_path / "missing.toml"),
        subprocess_factory=factory,
    )

    output = await bridge.run_command("printf 'hello'")

    assert output == "hello"


@pytest.mark.anyio
async def test_set_volume_runs_wpctl_commands(tmp_path: Path) -> None:
    """Setting volume should unmute first and then set the requested percentage."""

    factory = FakeSubprocessFactory(
        {
            ("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"): FakeProcess(stdout=""),
            ("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "30%"): FakeProcess(stdout=""),
        }
    )
    bridge = HyprlandBridge(
        config=load_config(tmp_path / "missing.toml"),
        subprocess_factory=factory,
    )

    result = await bridge.set_volume(30)

    assert result is True
    assert factory.calls == [
        ("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"),
        ("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "30%"),
    ]


@pytest.mark.anyio
async def test_start_audio_recording_uses_pw_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Microphone capture should start PipeWire recording and finalize on stop."""

    output_path = tmp_path / "capture.wav"
    process = FakeProcess(stdout="", stderr="", returncode=None)
    factory = FakeSubprocessFactory({})
    factory.responses = {
        (
            "/usr/bin/pw-record",
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
        ): process
    }
    monkeypatch.setattr("nyx.bridges.hyprland.shutil.which", lambda name: "/usr/bin/pw-record")
    bridge = HyprlandBridge(
        config=load_config(tmp_path / "missing.toml"),
        subprocess_factory=factory,
    )

    session = await bridge.start_audio_recording(str(output_path))
    output_path.write_bytes(b"RIFF" + (b"\x00" * 128))

    result = await session.stop()

    assert result is True
    assert process.terminated is True
