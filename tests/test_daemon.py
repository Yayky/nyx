"""Tests for Nyx daemon lifecycle behavior."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

import pytest

from nyx.bridges.stub import StubBridge
from nyx.config import load_config
from nyx.daemon import NyxDaemon
from nyx.intent_router import IntentRequest, IntentRouter
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeRegistry:
    """Minimal provider registry for daemon tests."""

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return a deterministic provider result without external I/O."""

        return ProviderQueryResult(
            provider_name=preferred_provider_name or "ollama-local",
            provider_type="fake",
            model_name=None,
            text="daemon provider response",
            fallback_used=False,
        )


def test_daemon_constructs_with_explicit_dependencies(tmp_path) -> None:
    """The daemon should accept fully injected dependencies."""

    config = load_config(tmp_path / "missing.toml")
    bridge = StubBridge("Linux")
    router = IntentRouter(config=config, bridge=bridge, provider_registry=FakeRegistry())

    daemon = NyxDaemon(config=config, bridge=bridge, router=router)

    assert daemon.config is config
    assert daemon.bridge is bridge
    assert daemon.router is router


@pytest.mark.anyio
async def test_daemon_run_forever_shuts_down_cleanly(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The daemon should idle until shutdown is requested."""

    config = load_config(tmp_path / "missing.toml")
    bridge = StubBridge("Linux")
    router = IntentRouter(config=config, bridge=bridge, provider_registry=FakeRegistry())
    daemon = NyxDaemon(config=config, bridge=bridge, router=router, logger=logging.getLogger("test"))

    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "add_signal_handler",
        lambda *args, **kwargs: None,
    )

    task = asyncio.create_task(daemon.run_forever())
    await asyncio.sleep(0)
    daemon.request_shutdown()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.anyio
async def test_daemon_handle_prompt_delegates_to_router(tmp_path) -> None:
    """Prompt handling should delegate directly to the router contract."""

    config = load_config(tmp_path / "missing.toml")
    bridge = StubBridge("Linux")
    router = IntentRouter(config=config, bridge=bridge, provider_registry=FakeRegistry())
    daemon = NyxDaemon(config=config, bridge=bridge, router=router)

    result = await daemon.handle_prompt(IntentRequest(text="hello", model_override=None, yolo=False))

    assert "daemon provider response" in result.response_text
