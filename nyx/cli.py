"""Command-line interface for Nyx.

The CLI wires together config loading, logging, bridge selection, provider
registry construction, daemon creation, and optional file-based voice
transcription. One-shot prompts route through the provider-backed intent router,
while daemon mode remains the long-running runtime entry point.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import logging
from pathlib import Path
import tempfile
import sys

from nyx.bridges.factory import get_system_bridge
from nyx.config import load_config
from nyx.control import send_control_command
from nyx.daemon import NyxDaemon
from nyx.intent_router import IntentRequest, IntentRouter
from nyx.logging import configure_logging
from nyx.monitors import SystemMonitorService
from nyx.providers.registry import ProviderRegistry
from nyx.skills import SkillsScheduler
from nyx.ui import run_launcher
from nyx.voice import VoiceInputError, VoiceTranscriber


def build_parser() -> argparse.ArgumentParser:
    """Create the current Nyx command-line parser."""

    parser = argparse.ArgumentParser(prog="nyx")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--daemon", action="store_true", help="Run the Nyx daemon.")
    mode_group.add_argument("--launcher", action="store_true", help="Run the GTK launcher.")
    mode_group.add_argument(
        "--toggle-ui",
        action="store_true",
        help="Toggle the managed Nyx overlay through the running daemon.",
    )
    mode_group.add_argument(
        "--voice",
        action="store_true",
        help="Record one microphone prompt, transcribe it locally, and route it once.",
    )
    parser.add_argument("--model", help="Override the configured default provider.")
    parser.add_argument(
        "--voice-file",
        help="Transcribe one local audio file with whisper.cpp and route the transcript once.",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Enable YOLO mode metadata for the current request.",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt text to route once.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Nyx CLI and return the process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging(logging.INFO)

    prompt = " ".join(args.prompt).strip()
    if args.voice_file and prompt:
        parser.print_usage(sys.stderr)
        sys.stderr.write("Use either a text prompt or --voice-file, not both.\n")
        return 2
    if args.voice and (args.voice_file or prompt):
        parser.print_usage(sys.stderr)
        sys.stderr.write("Use either a text prompt, --voice, or --voice-file.\n")
        return 2
    if args.voice_file and (args.daemon or args.launcher or args.toggle_ui):
        parser.print_usage(sys.stderr)
        sys.stderr.write("--voice-file is only supported for one-shot CLI mode.\n")
        return 2
    if not args.daemon and not args.launcher and not args.toggle_ui and not args.voice and not prompt and not args.voice_file:
        parser.print_usage(sys.stderr)
        sys.stderr.write(
            "Provide a prompt, use --voice/--voice-file for one-shot voice input, or use --daemon/--launcher/--toggle-ui.\n"
        )
        return 2

    try:
        if args.toggle_ui:
            asyncio.run(send_control_command("toggle"))
            return 0
        config = load_config()
        if args.yolo:
            config.system.yolo = True
        bridge = get_system_bridge(config=config, logger=logger)
        provider_registry = ProviderRegistry(config=config, logger=logger)
        router = IntentRouter(
            config=config,
            bridge=bridge,
            provider_registry=provider_registry,
            logger=logger,
        )
        daemon = NyxDaemon(
            config=config,
            bridge=bridge,
            router=router,
            skills_scheduler=SkillsScheduler(config=config, bridge=bridge, logger=logger),
            monitor_service=SystemMonitorService(config=config, bridge=bridge, logger=logger),
            logger=logger,
        )

        if args.daemon:
            return asyncio.run(daemon.run_forever()) or 0
        if args.launcher:
            return run_launcher(
                config=config,
                daemon=daemon,
                bridge=bridge,
                logger=logger,
                initial_prompt=prompt,
            )

        routed_prompt = prompt
        if args.voice:
            routed_prompt = asyncio.run(_record_voice_prompt(config, bridge, logger))
        if args.voice_file:
            routed_prompt = asyncio.run(_transcribe_voice_file(config, logger, Path(args.voice_file)))

        result = asyncio.run(
            daemon.handle_prompt(
                IntentRequest(
                    text=routed_prompt,
                    model_override=args.model,
                    yolo=args.yolo,
                )
            )
        )
        sys.stdout.write(f"{result.response_text}\n")
        return 0
    except Exception:
        logger.exception("Nyx CLI failed to start.")
        return 1


async def _transcribe_voice_file(
    config,
    logger: logging.Logger,
    audio_path: Path,
) -> str:
    """Transcribe one existing audio file into a prompt string."""

    _ensure_voice_enabled(config)
    transcriber = VoiceTranscriber(config=config, logger=logger)
    transcript = await transcriber.transcribe_file(audio_path)
    logger.info("Transcribed voice input to %d characters.", len(transcript))
    return transcript


async def _record_voice_prompt(config, bridge, logger: logging.Logger) -> str:
    """Record one microphone prompt, then transcribe it into text."""

    _ensure_voice_enabled(config)
    transcriber = VoiceTranscriber(config=config, logger=logger)
    with tempfile.TemporaryDirectory(prefix="nyx-voice-live-") as temp_dir:
        output_path = Path(temp_dir) / "microphone.wav"
        session = await bridge.start_audio_recording(str(output_path))
        sys.stderr.write("Recording from the default microphone. Press Enter to stop.\n")
        sys.stderr.flush()
        await asyncio.to_thread(sys.stdin.readline)
        recorded = await session.stop()
        if not recorded:
            raise VoiceInputError(
                "Microphone recording did not produce usable audio. Check your microphone and PipeWire input."
            )
        transcript = await transcriber.transcribe_file(output_path)
    logger.info("Transcribed voice input to %d characters.", len(transcript))
    return transcript


def _ensure_voice_enabled(config) -> None:
    """Reject voice input when the feature is disabled in config."""

    if not config.voice.enabled:
        raise VoiceInputError(
            "Voice input is disabled in config. Set [voice].enabled = true to use microphone or file STT input."
        )
