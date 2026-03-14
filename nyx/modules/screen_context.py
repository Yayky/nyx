"""Screen context module for Nyx.

Phase 11 adds explicit screenshot-based screen understanding. The module uses
``SystemBridge`` for active-window lookup and screenshot capture, then sends the
image plus lightweight context to a vision-capable provider.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re

from nyx.bridges.base import SystemBridge, WindowInfo
from nyx.config import NyxConfig
from nyx.providers.registry import ProviderRegistry

_SCREEN_PATTERNS = (
    re.compile(r"\b(what|describe|analyze|analyse)\b.+\b(screen|screenshot|display)\b", re.IGNORECASE),
    re.compile(r"\bwhat'?s on (my|the) screen\b", re.IGNORECASE),
    re.compile(r"\bwhat can you see\b", re.IGNORECASE),
    re.compile(r"\blook at (my|the) screen\b", re.IGNORECASE),
)


@dataclass(slots=True)
class ScreenContextResult:
    """Structured result returned by the Phase 11 screen-context module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    screenshot_path: Path


class ScreenContextModule:
    """Capture the current screen and answer explicit vision requests."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit bridge and provider dependencies."""

        self.config = config
        self.bridge = bridge
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.screen_context")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether a prompt is an explicit screen-understanding request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _SCREEN_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> ScreenContextResult:
        """Capture a screenshot and query a vision-capable provider."""

        active_window = await self.bridge.get_active_window()
        screenshot_path = self.config.system.screenshot_tmp
        captured = await self.bridge.screenshot(str(screenshot_path))
        if not captured:
            raise RuntimeError(f"Nyx could not capture a screenshot to {screenshot_path}.")

        provider_result = await self.provider_registry.query_with_image(
            prompt=self._build_vision_prompt(request_text),
            image_path=screenshot_path,
            context=self._vision_context(active_window),
            preferred_provider_name=model_override,
        )
        return ScreenContextResult(
            response_text=provider_result.text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.degraded,
            screenshot_path=screenshot_path,
        )

    def _build_vision_prompt(self, request_text: str) -> str:
        """Build the vision prompt sent to the provider."""

        return (
            "You are Nyx's Phase 11 screen-context module. "
            "Answer the user's question using the current screenshot and active window context. "
            "Be concrete and concise. If the screenshot is unclear, say so explicitly.\n\n"
            f"User request: {request_text}"
        )

    def _vision_context(self, active_window: WindowInfo) -> dict[str, str | None]:
        """Return active-window metadata bundled with the screenshot request."""

        return {
            "module": "screen_context",
            "active_window_app": active_window.app_name,
            "active_window_title": active_window.window_title,
            "active_window_workspace": active_window.workspace,
        }
