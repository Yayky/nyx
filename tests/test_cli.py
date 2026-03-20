"""Tests for the Phase 1 Nyx CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from nyx.bridges.base import AudioRecordingSession
from nyx import cli
from nyx.config import load_config as load_config_from_module
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeRegistry:
    """Minimal provider registry used to isolate CLI tests from live models."""

    config: Any
    logger: Any = None
    response_text: str = "fake provider response"

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return a deterministic provider result for CLI tests."""

        provider_name = preferred_provider_name or self.config.models.default
        return ProviderQueryResult(
            provider_name=provider_name,
            provider_type="fake",
            model_name=provider_name,
            text=f"{self.response_text}:{prompt}",
            fallback_used=False,
        )


class FakeVoiceTranscriber:
    """Minimal transcriber used to isolate CLI voice tests."""

    def __init__(self, config: Any, logger: Any = None) -> None:
        """Store injected dependencies for parity with the real transcriber."""

        self.config = config
        self.logger = logger

    async def transcribe_file(self, audio_path: Path) -> str:
        """Return a deterministic transcript for the supplied path."""

        return f"transcribed:{audio_path.name}"


class FakeBridge:
    """Minimal bridge stub used for CLI microphone tests."""

    def __init__(self) -> None:
        """Store capture bookkeeping for assertions."""

        self.recorded_paths: list[str] = []

    async def start_audio_recording(self, path: str) -> AudioRecordingSession:
        """Pretend to start recording and create the target file on stop."""

        self.recorded_paths.append(path)
        output_path = Path(path)

        async def _stop() -> bool:
            output_path.write_bytes(b"RIFF" + (b"\x00" * 128))
            return True

        return AudioRecordingSession(stop_callback=_stop)


def test_prompt_path_routes_once(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-shot prompt should print the router stub response to stdout."""

    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(tmp_path / "missing.toml"),
    )
    monkeypatch.setattr("nyx.cli.ProviderRegistry", FakeRegistry)

    exit_code = cli.main(["hello", "world"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "fake provider response:hello world" in captured.out


def test_no_args_exits_non_zero_with_guidance(capsys: pytest.CaptureFixture[str]) -> None:
    """Calling Nyx without prompt or daemon flag should return usage guidance."""

    exit_code = cli.main([])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert (
        "Provide a prompt, use --voice/--voice-file for one-shot voice input, or use --daemon/--launcher/--workspace/--admin/--toggle-ui/--show-ui/--hide-ui."
        in captured.err
    )


def test_invalid_toml_returns_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI startup should fail cleanly when config loading raises."""

    config_path = tmp_path / "config.toml"
    config_path.write_text("[system\n")

    monkeypatch.setattr("nyx.cli.load_config", lambda: load_config_from_module(config_path))

    exit_code = cli.main(["hello"])

    assert exit_code == 1


def test_daemon_flag_invokes_daemon_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The daemon flag should execute the daemon lifecycle entry point."""

    called = {"run": False}

    async def fake_run_forever(self) -> None:
        called["run"] = True

    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(tmp_path / "missing.toml"),
    )
    monkeypatch.setattr("nyx.cli.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr("nyx.cli.NyxDaemon.run_forever", fake_run_forever)

    exit_code = cli.main(["--daemon"])

    assert exit_code == 0
    assert called["run"] is True


def test_launcher_flag_invokes_launcher_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The launcher flag should execute the GTK launcher entry point."""

    called = {"launcher": False, "prompt": ""}

    def fake_run_launcher(*, config, daemon, bridge, logger, initial_prompt: str = "") -> int:
        called["launcher"] = True
        called["prompt"] = initial_prompt
        return 0

    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(tmp_path / "missing.toml"),
    )
    monkeypatch.setattr("nyx.cli.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr("nyx.cli.run_launcher", fake_run_launcher)

    exit_code = cli.main(["--launcher", "prefill", "prompt"])

    assert exit_code == 0
    assert called["launcher"] is True
    assert called["prompt"] == "prefill prompt"


def test_workspace_flag_invokes_workspace_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The workspace flag should execute the standalone workspace entry point."""

    called = {"workspace": False, "initial_section": ""}

    def fake_run_workspace(*, config, logger, initial_section: str = "workspace") -> int:
        called["workspace"] = True
        called["initial_section"] = initial_section
        return 0

    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(tmp_path / "missing.toml"),
    )
    monkeypatch.setattr(
        "nyx.cli.get_system_bridge",
        lambda *args, **kwargs: pytest.fail("--workspace should not initialize the system bridge"),
    )
    monkeypatch.setattr(
        "nyx.cli.ProviderRegistry",
        lambda *args, **kwargs: pytest.fail("--workspace should not initialize provider routing"),
    )
    monkeypatch.setattr("nyx.cli.run_workspace", fake_run_workspace)

    exit_code = cli.main(["--workspace"])

    assert exit_code == 0
    assert called["workspace"] is True
    assert called["initial_section"] == "workspace"


def test_admin_flag_opens_workspace_database_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The admin alias should open the workspace focused on Database."""

    called = {"workspace": False, "initial_section": ""}

    def fake_run_workspace(*, config, logger, initial_section: str = "workspace") -> int:
        called["workspace"] = True
        called["initial_section"] = initial_section
        return 0

    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(tmp_path / "missing.toml"),
    )
    monkeypatch.setattr(
        "nyx.cli.get_system_bridge",
        lambda *args, **kwargs: pytest.fail("--admin should not initialize the system bridge"),
    )
    monkeypatch.setattr(
        "nyx.cli.ProviderRegistry",
        lambda *args, **kwargs: pytest.fail("--admin should not initialize provider routing"),
    )
    monkeypatch.setattr("nyx.cli.run_workspace", fake_run_workspace)

    exit_code = cli.main(["--admin"])

    assert exit_code == 0
    assert called["workspace"] is True
    assert called["initial_section"] == "database"


def test_toggle_ui_sends_control_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """The toggle flag should send one control command to the running daemon."""

    called = {"command": None}

    async def fake_send_control_command(command: str):
        called["command"] = command
        return {"ok": True, "visible": True}

    monkeypatch.setattr("nyx.cli.send_control_command", fake_send_control_command)

    exit_code = cli.main(["--toggle-ui"])

    assert exit_code == 0
    assert called["command"] == "toggle"


def test_show_ui_sends_control_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """The show-ui flag should send one control command to the running daemon."""

    called = {"command": None}

    async def fake_send_control_command(command: str):
        called["command"] = command
        return {"ok": True, "visible": True}

    monkeypatch.setattr("nyx.cli.send_control_command", fake_send_control_command)

    exit_code = cli.main(["--show-ui"])

    assert exit_code == 0
    assert called["command"] == "show"


def test_hide_ui_sends_control_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hide-ui flag should send one control command to the running daemon."""

    called = {"command": None}

    async def fake_send_control_command(command: str):
        called["command"] = command
        return {"ok": True, "visible": False}

    monkeypatch.setattr("nyx.cli.send_control_command", fake_send_control_command)

    exit_code = cli.main(["--hide-ui"])

    assert exit_code == 0
    assert called["command"] == "hide"


def test_voice_file_routes_transcript_once(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A voice file should be transcribed first, then routed like any prompt."""

    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")

    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(tmp_path / "missing.toml"),
    )
    monkeypatch.setattr("nyx.cli.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr("nyx.cli.VoiceTranscriber", FakeVoiceTranscriber)

    exit_code = cli.main(["--voice-file", str(audio_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "fake provider response:transcribed:sample.wav" in captured.out


def test_voice_file_cannot_be_combined_with_prompt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Voice-file mode should reject an additional plain-text prompt."""

    exit_code = cli.main(["--voice-file", "sample.wav", "hello"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Use either a text prompt or --voice-file, not both." in captured.err


def test_voice_flag_records_and_routes_transcript_once(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Microphone mode should record, transcribe, and route one prompt."""

    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(tmp_path / "missing.toml"),
    )
    monkeypatch.setattr("nyx.cli.get_system_bridge", lambda config, logger=None: FakeBridge())
    monkeypatch.setattr("nyx.cli.ProviderRegistry", FakeRegistry)
    monkeypatch.setattr("nyx.cli.VoiceTranscriber", FakeVoiceTranscriber)

    async def fake_to_thread(func, *args):
        return ""

    monkeypatch.setattr("nyx.cli.asyncio.to_thread", fake_to_thread)

    exit_code = cli.main(["--voice"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Recording from the default microphone." in captured.err
    assert "fake provider response:transcribed:microphone.wav" in captured.out


def test_voice_input_respects_disabled_config(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice input modes should fail fast when disabled in config."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[voice]
enabled = false
""".strip()
    )
    monkeypatch.setattr(
        "nyx.cli.load_config",
        lambda: load_config_from_module(config_path),
    )

    with caplog.at_level("ERROR"):
        exit_code = cli.main(["--voice-file", str(tmp_path / "sample.wav")])

    assert exit_code == 1
    assert "Voice input is disabled in config" in caplog.text


def test_python_module_entrypoint_matches_cli(tmp_path: Path) -> None:
    """``python -m nyx`` should route prompts through the same CLI path."""

    repo_root = Path(__file__).resolve().parents[1]
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".config/nyx"
    config_dir.mkdir(parents=True)
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(
        """
import json
import sys

prompt = sys.stdin.read().strip()
print(json.dumps({"response": f"fake subprocess:{prompt}"}))
""".strip()
    )
    (config_dir / "config.toml").write_text(
        f"""
[models]
default = "fixture-cli"
fallback = []

[[models.providers]]
name = "fixture-cli"
type = "subprocess-cli"
binary = "{sys.executable}"
args = ["{fake_cli}", "-"]
timeout_seconds = 5
""".strip()
    )
    result = subprocess.run(
        [sys.executable, "-m", "nyx", "hello"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env={
            **dict(),
            "HOME": str(fake_home),
            "PATH": str(Path(sys.executable).parent),
        },
    )

    assert result.returncode == 0
    assert "fake subprocess:hello" in result.stdout
