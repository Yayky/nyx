"""Tests for the Phase 18 whisper.cpp transcription service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nyx.config import load_config
from nyx.voice.transcriber import CommandResult, VoiceInputError, VoiceTranscriber


def test_transcribe_file_returns_extracted_text(tmp_path: Path) -> None:
    """A successful whisper.cpp subprocess run should yield normalized text."""

    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")
    model_path = tmp_path / "ggml-base.bin"
    model_path.write_bytes(b"model")
    config = load_config(tmp_path / "missing.toml")
    transcriber = VoiceTranscriber(config=config)

    transcriber._resolve_whisper_binary = lambda: Path("/usr/bin/whisper-cli")  # type: ignore[method-assign]
    transcriber._resolve_model_path = lambda: model_path  # type: ignore[method-assign]

    async def fake_run_command(*command: str) -> CommandResult:
        assert command[0] == "/usr/bin/whisper-cli"
        assert "-m" in command
        assert "-f" in command
        return CommandResult(
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:01.000] hello there\nmain: done",
            stderr="",
        )

    transcriber._run_command = fake_run_command  # type: ignore[method-assign]

    transcript = asyncio.run(transcriber.transcribe_file(audio_path))

    assert transcript == "hello there"


def test_transcribe_file_reports_missing_audio(tmp_path: Path) -> None:
    """Missing voice input files should fail with a clear error."""

    config = load_config(tmp_path / "missing.toml")
    transcriber = VoiceTranscriber(config=config)

    with pytest.raises(VoiceInputError, match="Voice input file not found"):
        asyncio.run(transcriber.transcribe_file(tmp_path / "missing.wav"))


def test_extract_transcript_strips_whisper_console_noise(tmp_path: Path) -> None:
    """Console log lines and timestamps should be removed from the transcript."""

    config = load_config(tmp_path / "missing.toml")
    transcriber = VoiceTranscriber(config=config)

    transcript = transcriber._extract_transcript(
        "\n".join(
            [
                "system_info: AVX = 1",
                "[00:00:00.000 --> 00:00:01.000]  hello",
                "[00:00:01.000 --> 00:00:02.000] world",
                "main: processing complete",
            ]
        )
    )

    assert transcript == "hello world"


def test_transcribe_file_rejects_blank_audio_marker(tmp_path: Path) -> None:
    """whisper.cpp blank-audio markers should surface as a voice-input error."""

    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")
    model_path = tmp_path / "ggml-base.bin"
    model_path.write_bytes(b"model")
    config = load_config(tmp_path / "missing.toml")
    transcriber = VoiceTranscriber(config=config)

    transcriber._resolve_whisper_binary = lambda: Path("/usr/bin/whisper-cli")  # type: ignore[method-assign]
    transcriber._resolve_model_path = lambda: model_path  # type: ignore[method-assign]

    async def fake_run_command(*command: str) -> CommandResult:
        return CommandResult(returncode=0, stdout="[BLANK_AUDIO]\nmain: done", stderr="")

    transcriber._run_command = fake_run_command  # type: ignore[method-assign]

    with pytest.raises(VoiceInputError, match="blank audio"):
        asyncio.run(transcriber.transcribe_file(audio_path))
