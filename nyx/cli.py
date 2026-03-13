"""Command-line interface for Nyx.

The CLI wires together config loading, logging, bridge selection, provider
registry construction, and daemon creation. In Phase 2, one-shot prompts route
through the model provider layer while daemon mode remains the long-running
runtime entry point.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import logging
import sys

from nyx.bridges.factory import get_system_bridge
from nyx.config import load_config
from nyx.daemon import NyxDaemon
from nyx.intent_router import IntentRequest, IntentRouter
from nyx.logging import configure_logging
from nyx.providers.registry import ProviderRegistry
from nyx.ui import run_launcher


def build_parser() -> argparse.ArgumentParser:
    """Create the current Nyx command-line parser."""

    parser = argparse.ArgumentParser(prog="nyx")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--daemon", action="store_true", help="Run the Nyx daemon.")
    mode_group.add_argument("--launcher", action="store_true", help="Run the GTK launcher.")
    parser.add_argument("--model", help="Override the configured default provider.")
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
    if not args.daemon and not args.launcher and not prompt:
        parser.print_usage(sys.stderr)
        sys.stderr.write("Provide a prompt for one-shot mode or use --daemon/--launcher.\n")
        return 2

    try:
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
        daemon = NyxDaemon(config=config, bridge=bridge, router=router, logger=logger)

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

        result = asyncio.run(
            daemon.handle_prompt(
                IntentRequest(
                    text=prompt,
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
