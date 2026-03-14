"""Local whisper.cpp transcription support for Nyx.

Phase 18 adds offline speech-to-text through a configured ``whisper.cpp``
subprocess. The transcriber accepts an audio file path, converts it to the
16-bit mono WAV format required by the official ``whisper-cli`` workflow when
needed, and returns a plain text transcript that can be routed through Nyx like
any other prompt.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import tempfile

from nyx.config import NyxConfig


class VoiceInputError(RuntimeError):
    """Raised when Nyx cannot transcribe the requested audio input."""


@dataclass(slots=True)
class CommandResult:
    """Simple subprocess result used by the voice transcriber."""

    returncode: int
    stdout: str
    stderr: str


class VoiceTranscriber:
    """Transcribe one local audio file through ``whisper.cpp``."""

    def __init__(
        self,
        config: NyxConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the transcriber with config and logging dependencies."""

        self.config = config
        self.logger = logger or logging.getLogger("nyx.voice.transcriber")

    async def transcribe_file(self, audio_path: Path) -> str:
        """Transcribe one audio file into plain text.

        Args:
            audio_path: Input audio path supplied by the user.

        Returns:
            The normalized transcript text.

        Raises:
            VoiceInputError: The input file, model, or whisper subprocess could
                not be used to produce a transcript.
        """

        input_path = audio_path.expanduser()
        if not input_path.exists():
            raise VoiceInputError(f"Voice input file not found: {input_path}")

        binary_path = self._resolve_whisper_binary()
        model_path = self._resolve_model_path()

        with tempfile.TemporaryDirectory(prefix="nyx-voice-") as temp_dir:
            temp_root = Path(temp_dir)
            wav_path = temp_root / "input.wav"
            prepared_audio = await self._prepare_audio_input(input_path, wav_path)
            result = await self._run_command(
                str(binary_path),
                "-m",
                str(model_path),
                "-f",
                str(prepared_audio),
            )

        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "whisper.cpp failed"
            raise VoiceInputError(f"whisper.cpp transcription failed: {message}")

        transcript = self._extract_transcript(result.stdout)
        if not transcript:
            fallback = result.stderr.strip()
            if fallback:
                transcript = self._extract_transcript(fallback)
        if not transcript:
            raise VoiceInputError("whisper.cpp returned no parseable transcript.")
        return transcript

    def _resolve_whisper_binary(self) -> Path:
        """Resolve the configured whisper.cpp executable path."""

        configured = Path(self.config.voice.whisper_binary).expanduser()
        if configured.is_absolute() and configured.exists():
            return configured
        if configured.parent != Path(".") and configured.exists():
            return configured

        search_names = [self.config.voice.whisper_binary]
        if self.config.voice.whisper_binary == "whisper":
            search_names.append("whisper-cli")
        for candidate in search_names:
            resolved = shutil.which(candidate)
            if resolved:
                return Path(resolved)
        raise VoiceInputError(
            "whisper.cpp binary not found. Configure [voice].whisper_binary to the installed executable path."
        )

    def _resolve_model_path(self) -> Path:
        """Resolve the configured whisper model file path."""

        configured = self.config.voice.whisper_model
        configured_path = Path(configured).expanduser()
        if configured_path.exists():
            return configured_path

        model_name = configured
        file_candidates = []
        if model_name.endswith(".bin"):
            file_candidates.append(model_name)
        else:
            if model_name.startswith("ggml-"):
                file_candidates.append(f"{model_name}.bin")
            else:
                file_candidates.append(f"ggml-{model_name}.bin")

        search_roots = [
            Path("~/.local/share/nyx/whisper").expanduser(),
            Path("~/.cache/whisper.cpp").expanduser(),
            Path("~/.cache/whisper.cpp/models").expanduser(),
            Path("~/whisper.cpp/models").expanduser(),
        ]
        for root in search_roots:
            for file_name in file_candidates:
                candidate = root / file_name
                if candidate.exists():
                    return candidate

        searched = ", ".join(str(root / name) for root in search_roots for name in file_candidates)
        raise VoiceInputError(
            "whisper.cpp model file not found. Configure [voice].whisper_model to a model path "
            f"or place a ggml model in a standard location. Searched: {searched}"
        )

    async def _prepare_audio_input(self, input_path: Path, wav_path: Path) -> Path:
        """Convert arbitrary audio input into the 16-bit WAV format whisper.cpp expects."""

        if input_path.suffix.casefold() == ".wav":
            return input_path

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            raise VoiceInputError(
                "Non-WAV voice input requires ffmpeg for conversion to 16-bit WAV, but ffmpeg was not found."
            )

        result = await self._run_command(
            ffmpeg_path,
            "-y",
            "-i",
            str(input_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        )
        if result.returncode != 0 or not wav_path.exists():
            message = result.stderr.strip() or result.stdout.strip() or "ffmpeg conversion failed"
            raise VoiceInputError(f"ffmpeg audio conversion failed: {message}")
        return wav_path

    async def _run_command(self, *command: str) -> CommandResult:
        """Run one subprocess command asynchronously and capture its text output."""

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        return CommandResult(
            returncode=process.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )

    def _extract_transcript(self, raw_output: str) -> str:
        """Extract the spoken transcript from raw whisper.cpp console output."""

        lines: list[str] = []
        for raw_line in raw_output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("[") and "]" in line:
                line = line.split("]", 1)[1].strip()
            lower_line = line.casefold()
            if lower_line.startswith("whisper_") or lower_line.startswith("main:") or lower_line.startswith("system_info"):
                continue
            if " --> " in line:
                continue
            lines.append(line)

        return " ".join(lines).strip()
