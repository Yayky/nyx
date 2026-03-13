"""Tests for Nyx bridge selection and stub behavior."""

from __future__ import annotations

import logging

import pytest

from nyx.bridges.base import BridgeNotImplementedError
from nyx.bridges.factory import get_system_bridge
from nyx.bridges.hyprland import HyprlandBridge
from nyx.bridges.stub import StubBridge
from nyx.config import load_config


def test_linux_platform_returns_hyprland_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Linux should map to the Phase 3 Hyprland bridge."""

    monkeypatch.setattr("nyx.bridges.factory.platform.system", lambda: "Linux")
    config = load_config(tmp_path / "missing.toml")

    bridge = get_system_bridge(config=config)

    assert isinstance(bridge, HyprlandBridge)


def test_windows_platform_returns_windows_stub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Windows should map to the Phase 1 Windows stub bridge."""

    monkeypatch.setattr("nyx.bridges.factory.platform.system", lambda: "Windows")
    config = load_config(tmp_path / "missing.toml")

    bridge = get_system_bridge(config=config)

    assert isinstance(bridge, StubBridge)
    assert bridge.platform_name == "Windows"


def test_unsupported_platform_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Unsupported platforms should fail fast."""

    monkeypatch.setattr("nyx.bridges.factory.platform.system", lambda: "Darwin")
    config = load_config(tmp_path / "missing.toml")

    with pytest.raises(NotImplementedError, match="Darwin"):
        get_system_bridge(config=config)


@pytest.mark.anyio
async def test_stub_bridge_methods_raise_with_context(caplog: pytest.LogCaptureFixture) -> None:
    """Stub bridge calls should log and raise explicit Phase 1 errors."""

    bridge = StubBridge("Linux", logger=logging.getLogger("test.stub"))

    with caplog.at_level(logging.ERROR):
        with pytest.raises(BridgeNotImplementedError, match="get_system_stats"):
            await bridge.get_system_stats()

    assert "Phase 1" in caplog.text
