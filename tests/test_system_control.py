"""Tests for the Phase 6 system-control module."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import pytest

from nyx.bridges.base import BridgeConfirmationRequiredError, WindowInfo
from nyx.config import load_config
from nyx.modules.system_control import SystemControlModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeProviderRegistry:
    """Minimal provider registry stub used to test module planning."""

    result: ProviderQueryResult
    seen_prompt: str | None = None
    seen_context: dict[str, Any] | None = None
    seen_preferred_provider: str | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return a deterministic planner response."""

        self.seen_prompt = prompt
        self.seen_context = context
        self.seen_preferred_provider = preferred_provider_name
        return self.result


@dataclass
class FakeBridge:
    """Bridge stub implementing only the methods exercised in these tests."""

    volume_calls: list[int] | None = None
    command_error: Exception | None = None

    async def get_active_window(self) -> WindowInfo:
        """Return a fixed active window."""

        return WindowInfo(app_name="kitty", window_title="test", workspace="1")

    async def move_window_to_workspace(self, window: str, workspace: str) -> bool:
        """Return success for move-window tests."""

        del window, workspace
        return True

    async def list_windows(self) -> list[WindowInfo]:
        """Return a deterministic window list."""

        return [WindowInfo(app_name="kitty", window_title="shell", workspace="1")]

    async def screenshot(self, path: str) -> bool:
        """Succeed for screenshot tests."""

        del path
        return True

    async def run_command(self, command: str, confirm_if_destructive: bool = True) -> str:
        """Return command output or raise a configured bridge error."""

        del confirm_if_destructive
        if self.command_error is not None:
            raise self.command_error
        return f"ran:{command}"

    async def list_processes(self) -> list[dict]:
        """Return a single fake process."""

        return [{"pid": 123, "name": "kitty", "command": "kitty"}]

    async def kill_process(self, identifier: str) -> bool:
        """Pretend the kill succeeded."""

        del identifier
        return True

    async def set_brightness(self, percent: int) -> bool:
        """Pretend brightness was changed."""

        del percent
        return True

    async def set_volume(self, percent: int) -> bool:
        """Record the requested volume percentage."""

        if self.volume_calls is None:
            self.volume_calls = []
        self.volume_calls.append(percent)
        return True

    async def get_system_stats(self) -> dict:
        """Return deterministic stats payload."""

        return {"cpu_count": 8}

    async def notify(self, title: str, body: str) -> None:
        """Pretend the notification succeeded."""

        del title, body


@pytest.mark.anyio
async def test_system_control_executes_bridge_action_from_planner_json(tmp_path) -> None:
    """The system-control module should execute bridge actions from planner JSON."""

    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"set_volume","arguments":{"percent":30},"rationale":"adjust audio"}',
            fallback_used=False,
        )
    )
    bridge = FakeBridge()
    module = SystemControlModule(
        config=load_config(tmp_path / "missing.toml"),
        bridge=bridge,
        provider_registry=registry,
        logger=logging.getLogger("test.system_control"),
    )

    result = await module.handle("set the volume to 30 percent", model_override="codex-cli")

    assert bridge.volume_calls == [30]
    assert result.response_text == "Volume set to 30%."
    assert result.used_model == "codex-cli"
    assert registry.seen_preferred_provider == "codex-cli"
    assert registry.seen_context is not None
    assert "allowed_operations" in registry.seen_context


@pytest.mark.anyio
async def test_system_control_parses_fenced_json_and_uses_default_screenshot_path(tmp_path) -> None:
    """Fenced JSON planner output should still be parsed and executed."""

    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="ollama-local",
            provider_type="ollama",
            model_name="qwen2.5:7b",
            text=(
                "```json\n"
                '{"operation":"screenshot","arguments":{},"rationale":"capture current screen"}'
                "\n```"
            ),
            fallback_used=True,
        )
    )
    bridge = FakeBridge()
    module = SystemControlModule(
        config=load_config(tmp_path / "missing.toml"),
        bridge=bridge,
        provider_registry=registry,
        logger=logging.getLogger("test.system_control"),
    )

    result = await module.handle("take a screenshot")

    assert result.degraded is True
    assert result.response_text == "Screenshot saved to /tmp/nyx-screen.png."


@pytest.mark.anyio
async def test_system_control_surfaces_confirmation_required_errors(tmp_path) -> None:
    """Bridge confirmation failures should become user-facing responses."""

    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"run_command","arguments":{"command":"rm -rf ~/tmp"}}',
            fallback_used=False,
        )
    )
    bridge = FakeBridge(
        command_error=BridgeConfirmationRequiredError("Need confirmation before deleting files.")
    )
    module = SystemControlModule(
        config=load_config(tmp_path / "missing.toml"),
        bridge=bridge,
        provider_registry=registry,
        logger=logging.getLogger("test.system_control"),
    )

    result = await module.handle("delete ~/tmp")

    assert result.response_text == "Need confirmation before deleting files."


def test_system_control_matcher_is_conservative() -> None:
    """System-control routing should match obvious bridge-backed requests only."""

    assert SystemControlModule.matches_request("set the volume to 20 percent") is True
    assert SystemControlModule.matches_request("show me the active window") is True
    assert SystemControlModule.matches_request("write a poem about screens") is False
