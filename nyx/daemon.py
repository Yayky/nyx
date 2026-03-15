"""Async daemon lifecycle for Nyx."""

from __future__ import annotations

import asyncio
import logging
import platform
import signal

from nyx.bridges.base import SystemBridge
from nyx.bridges.factory import get_system_bridge
from nyx.config import NyxConfig
from nyx.control import OverlayControlService
from nyx.intent_router import IntentRequest, IntentResult, IntentRouter
from nyx.monitors import SystemMonitorService
from nyx.providers.registry import ProviderRegistry
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
        self.logger = logger or logging.getLogger("nyx.daemon")
        self.provider_registry = router.provider_registry
        self.skills_scheduler = skills_scheduler
        self.monitor_service = monitor_service
        self.overlay_control_service = overlay_control_service or OverlayControlService(
            reload_callback=self._reload_config_from_disk,
            logger=self.logger,
        )
        self._shutdown_event = asyncio.Event()
        self._running = False

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
            self._running = True
            await self._start_services()
            await self._shutdown_event.wait()
        except Exception:
            self.logger.exception("Nyx daemon encountered an unrecoverable runtime error.")
            raise
        finally:
            await self._stop_services()
            self._running = False
            self.logger.info("Nyx daemon shutting down.")

    async def handle_prompt(self, request: IntentRequest) -> IntentResult:
        """Route a single prompt through the configured intent router."""

        return await self.router.route(request)

    async def reload_config(self, new_config: NyxConfig) -> None:
        """Reload config-bound runtime state while the daemon stays alive."""

        restart_skills = self.skills_scheduler is not None and self._running
        restart_monitors = self.monitor_service is not None and self._running
        if restart_monitors and self.monitor_service is not None:
            await self.monitor_service.stop()
        if restart_skills and self.skills_scheduler is not None:
            await self.skills_scheduler.stop()

        self.config = new_config
        self.bridge = get_system_bridge(config=new_config, logger=self.logger)
        self.provider_registry = ProviderRegistry(config=new_config, logger=self.logger)
        self.router.bridge = self.bridge
        self.router.reload_config(new_config, self.provider_registry)
        self.skills_scheduler = SkillsScheduler(config=new_config, bridge=self.bridge, logger=self.logger)
        self.monitor_service = SystemMonitorService(config=new_config, bridge=self.bridge, logger=self.logger)

        if self.overlay_control_service is not None:
            self.overlay_control_service.reload_callback = self._reload_config_from_disk

        if restart_skills and self.skills_scheduler is not None:
            await self.skills_scheduler.start()
        if restart_monitors and self.monitor_service is not None:
            await self.monitor_service.start()

    def request_shutdown(self) -> None:
        """Signal the daemon to stop waiting and shut down."""

        self._shutdown_event.set()

    async def _reload_config_from_disk(self) -> None:
        """Reload the daemon config from the existing config path."""

        from nyx.config import load_config

        await self.reload_config(load_config(self.config.config_path))

    async def _start_services(self) -> None:
        """Start background daemon services."""

        if self.skills_scheduler is not None:
            await self.skills_scheduler.start()
        if self.monitor_service is not None:
            await self.monitor_service.start()
        if self.overlay_control_service is not None:
            await self.overlay_control_service.start()

    async def _stop_services(self) -> None:
        """Stop background daemon services."""

        if self.monitor_service is not None:
            await self.monitor_service.stop()
        if self.skills_scheduler is not None:
            await self.skills_scheduler.stop()
        if self.overlay_control_service is not None:
            await self.overlay_control_service.stop()

    def _install_signal_handlers(self) -> None:
        """Install SIGINT and SIGTERM handlers when the loop supports them."""

        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self.request_shutdown)
            except (NotImplementedError, RuntimeError):
                self.logger.debug("Signal handler not available for %s", signum.name)
