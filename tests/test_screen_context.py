"""Tests for the Phase 11 screen-context module."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.bridges.base import WindowInfo
from nyx.config import load_config
from nyx.modules.screen_context import ScreenContextModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeVisionRegistry:
    """Minimal registry stub for vision-query tests."""

    result: ProviderQueryResult
    seen_prompt: str | None = None
    seen_context: dict[str, Any] | None = None
    seen_image_path: Path | None = None
    seen_preferred_provider_name: str | None = None

    async def query_with_image(
        self,
        prompt: str,
        image_path: Path,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return the configured vision result."""

        self.seen_prompt = prompt
        self.seen_context = context
        self.seen_image_path = image_path
        self.seen_preferred_provider_name = preferred_provider_name
        return self.result


@dataclass
class FakeBridge:
    """Bridge stub for screen-context tests."""

    screenshot_calls: list[str] | None = None

    async def get_active_window(self) -> WindowInfo:
        """Return a deterministic active window."""

        return WindowInfo(app_name="brave-browser", window_title="Nyx docs", workspace="3")

    async def screenshot(self, path: str) -> bool:
        """Pretend to capture a screenshot and record the target path."""

        if self.screenshot_calls is None:
            self.screenshot_calls = []
        self.screenshot_calls.append(path)
        Path(path).write_bytes(b"fakepng")
        return True


@pytest.mark.anyio
async def test_screen_context_module_captures_and_queries_with_image(tmp_path: Path) -> None:
    """The screen-context module should capture the screen and call the vision path."""

    config = load_config(tmp_path / "config.toml")
    config.system.screenshot_tmp = tmp_path / "screen.png"
    registry = FakeVisionRegistry(
        result=ProviderQueryResult(
            provider_name="openai",
            provider_type="openai",
            model_name="gpt-4o",
            text="The screenshot shows a browser window with Nyx documentation.",
            fallback_used=False,
        )
    )
    bridge = FakeBridge()
    module = ScreenContextModule(
        config=config,
        bridge=bridge,  # type: ignore[arg-type]
        provider_registry=registry,  # type: ignore[arg-type]
        logger=logging.getLogger("test"),
    )

    result = await module.handle("what is on my screen?", model_override="openai")

    assert bridge.screenshot_calls == [str(config.system.screenshot_tmp)]
    assert result.response_text.startswith("The screenshot shows")
    assert registry.seen_image_path == config.system.screenshot_tmp
    assert registry.seen_preferred_provider_name == "openai"
    assert registry.seen_context is not None
    assert registry.seen_context["active_window_app"] == "brave-browser"


def test_screen_context_matcher_is_conservative() -> None:
    """Only explicit screen-analysis prompts should route into Phase 11."""

    assert ScreenContextModule.matches_request("what is on my screen?") is True
    assert ScreenContextModule.matches_request("describe the screenshot") is True
    assert ScreenContextModule.matches_request("look at my screen and tell me what you see") is True
    assert ScreenContextModule.matches_request("take a screenshot") is False
