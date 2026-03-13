"""Tests for shared overlay session/controller behavior."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.bridges.base import WindowInfo
from nyx.config import load_config
from nyx.intent_router import IntentResult
from nyx.ui.session import OverlaySessionController


@dataclass
class FakeBridge:
    """Bridge stub returning a deterministic active window."""

    async def get_active_window(self) -> WindowInfo:
        """Return a fixed active window used by launcher tests."""

        return WindowInfo(app_name="kitty", window_title="launcher-test", workspace="1")


@dataclass
class FakeDaemon:
    """Daemon stub returning a deterministic intent result."""

    result: IntentResult

    async def handle_prompt(self, request: Any) -> IntentResult:
        """Return the configured result for any launcher prompt."""

        return self.result


@pytest.mark.anyio
async def test_overlay_controller_maps_result_to_view_state(tmp_path: Path) -> None:
    """Submitting a prompt should produce launcher-friendly state."""

    controller = OverlaySessionController(
        daemon=FakeDaemon(
            IntentResult(
                response_text="hello from provider",
                intent="unclassified",
                target_module=None,
                used_model="codex-cli",
                degraded=False,
                model_name=None,
                token_count=None,
            )
        ),
        bridge=FakeBridge(),
        config=load_config(tmp_path / "missing.toml"),
        logger=logging.getLogger("test.launcher"),
    )

    state = await controller.submit_prompt("hello")

    assert state.response_text == "hello from provider"
    assert state.provider_name == "codex-cli"
    assert state.active_window == WindowInfo(
        app_name="kitty",
        window_title="launcher-test",
        workspace="1",
    )
    assert state.selected_session_id == 1
    assert controller.sessions[0].title == "Session 1"


def test_overlay_history_navigation(tmp_path: Path) -> None:
    """History navigation should cycle through prior prompts predictably."""

    controller = OverlaySessionController(
        daemon=FakeDaemon(
            IntentResult(
                response_text="ok",
                intent="unclassified",
                target_module=None,
                used_model="ollama-local",
                degraded=False,
                model_name=None,
                token_count=None,
            )
        ),
        bridge=FakeBridge(),
        config=load_config(tmp_path / "missing.toml"),
        logger=logging.getLogger("test.launcher"),
    )

    controller.history = ["first", "second", "third"]

    assert controller.previous_history() == "third"
    assert controller.previous_history() == "second"
    assert controller.next_history() == "third"
    assert controller.next_history() == ""


@pytest.mark.anyio
async def test_overlay_controller_filters_and_restores_sessions(tmp_path: Path) -> None:
    """Panel-mode session search and selection should work from shared state."""

    controller = OverlaySessionController(
        daemon=FakeDaemon(
            IntentResult(
                response_text="first response",
                intent="unclassified",
                target_module=None,
                used_model="ollama-local",
                degraded=False,
                model_name="qwen2.5:7b",
                token_count=None,
            )
        ),
        bridge=FakeBridge(),
        config=load_config(tmp_path / "missing.toml"),
        logger=logging.getLogger("test.launcher"),
    )

    await controller.submit_prompt("alpha task")
    controller.daemon = FakeDaemon(
        IntentResult(
            response_text="second response",
            intent="unclassified",
            target_module=None,
            used_model="codex-cli",
            degraded=False,
            model_name=None,
            token_count=None,
        )
    )
    await controller.submit_prompt("beta search")

    matches = controller.filter_sessions("beta")
    assert [session.session_id for session in matches] == [2]

    restored = controller.state_for_session(1)
    assert restored is not None
    assert restored.response_text == "first response"
    assert restored.selected_session_id == 1
