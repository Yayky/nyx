"""Async daemon lifecycle for Nyx.

The Phase 1 daemon wires together configuration, bridge selection, and routing,
then idles cleanly under asyncio until it receives a shutdown signal. Future
phases will attach background services and IPC on top of this lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import signal

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig
from nyx.control import OverlayControlService
from nyx.intent_router import IntentRequest, IntentResult, IntentRouter
from nyx.monitors import SystemMonitorService
from nyx.skills import SkillsScheduler


class NyxDaemon:
    """Core Nyx daemon runtime container."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        router: IntentRouter,
        skills_scheduler: SkillsScheduler | None = None,
        monitor_service: SystemMonitorService | None = None,
        overlay_control_service: OverlayControlService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the daemon with explicit dependencies."""

        self.config = config
        self.bridge = bridge
        self.router = router
        self.skills_scheduler = skills_scheduler
        self.monitor_service = monitor_service
        self.overlay_control_service = overlay_control_service or OverlayControlService(logger=logger)
        self.logger = logger or logging.getLogger("nyx.daemon")
        self._shutdown_event = asyncio.Event()

    async def run_forever(self) -> None:
        """Start the daemon lifecycle and wait for shutdown."""

        self._install_signal_handlers()
        self.logger.info(
            "Nyx daemon starting on platform=%s bridge=%s default_model=%s",
            platform.system(),
            self.bridge.__class__.__name__,
            self.config.models.default,
        )
        try:
            if self.skills_scheduler is not None:
                await self.skills_scheduler.start()
            if self.monitor_service is not None:
                await self.monitor_service.start()
            if self.overlay_control_service is not None:
                await self.overlay_control_service.start()
            await self._shutdown_event.wait()
        except Exception:
            self.logger.exception("Nyx daemon encountered an unrecoverable runtime error.")
            raise
        finally:
            if self.monitor_service is not None:
                await self.monitor_service.stop()
            if self.skills_scheduler is not None:
                await self.skills_scheduler.stop()
            if self.overlay_control_service is not None:
                await self.overlay_control_service.stop()
            self.logger.info("Nyx daemon shutting down.")

    async def handle_prompt(self, request: IntentRequest) -> IntentResult:
        """Route a single prompt through the configured intent router."""

        return await self.router.route(request)

    def request_shutdown(self) -> None:
        """Signal the daemon to stop waiting and shut down."""

        self._shutdown_event.set()

    def _install_signal_handlers(self) -> None:
        """Install SIGINT and SIGTERM handlers when the loop supports them."""

        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self.request_shutdown)
            except (NotImplementedError, RuntimeError):
                self.logger.debug("Signal handler not available for %s", signum.name)
