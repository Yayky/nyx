"""Tests for the Phase 17 system monitor module and polling service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.config import load_config
from nyx.monitors import MonitorRule, MonitorsStore, SystemMonitorService
from nyx.modules.system_monitor import SystemMonitorModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeProviderRegistry:
    """Minimal registry stub for provider-planned monitor requests."""

    result: ProviderQueryResult
    seen_prompt: str | None = None
    seen_context: dict[str, Any] | None = None
    seen_preferred_provider_name: str | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return a deterministic provider-planning result."""

        self.seen_prompt = prompt
        self.seen_context = context
        self.seen_preferred_provider_name = preferred_provider_name
        return self.result


@dataclass
class FakeBridge:
    """Minimal notification bridge for proactive monitor tests."""

    notifications: list[tuple[str, str]] = field(default_factory=list)

    async def notify(self, title: str, body: str) -> None:
        """Record one notification instead of touching the real desktop."""

        self.notifications.append((title, body))


@pytest.mark.anyio
async def test_monitors_store_round_trips_rules(tmp_path: Path) -> None:
    """Monitor rules should persist cleanly in the TOML store."""

    store = MonitorsStore(tmp_path / "monitors.toml")
    await store.save_rules(
        [
            MonitorRule(
                rule_id="deadbeef",
                name="High RAM",
                metric="memory_percent",
                operator="gt",
                threshold=90,
                message="RAM is high",
                cooldown_seconds=120,
                enabled=True,
            )
        ]
    )

    rules = await store.load_rules()

    assert len(rules) == 1
    assert rules[0].name == "High RAM"
    assert rules[0].metric == "memory_percent"


@pytest.mark.anyio
async def test_system_monitor_module_adds_rule(tmp_path: Path) -> None:
    """Add-monitor requests should persist one new rule to ``monitors.toml``."""

    config = load_config(tmp_path / "config.toml")
    store = MonitorsStore(tmp_path / "monitors.toml")
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"add_monitor","arguments":{"name":"High RAM","metric":"memory_percent","operator":"gt","threshold":90,"message":"Memory is high","cooldown_seconds":180}}',
            fallback_used=False,
        )
    )
    module = SystemMonitorModule(
        config=config,
        provider_registry=registry,
        store=store,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("alert me if memory usage exceeds 90 percent", model_override="codex-cli")

    rules = await store.load_rules()
    assert "Added monitor 'High RAM'" in result.response_text
    assert len(rules) == 1
    assert rules[0].metric == "memory_percent"
    assert rules[0].cooldown_seconds == 180


@pytest.mark.anyio
async def test_system_monitor_module_lists_rules(tmp_path: Path) -> None:
    """List requests should render current configured proactive monitor rules."""

    config = load_config(tmp_path / "config.toml")
    store = MonitorsStore(tmp_path / "monitors.toml")
    await store.save_rules(
        [
            MonitorRule(
                rule_id="deadbeef",
                name="High CPU",
                metric="cpu_percent",
                operator="gt",
                threshold=85,
                message="CPU high",
                cooldown_seconds=300,
                enabled=True,
            )
        ]
    )
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"list_monitors","arguments":{}}',
            fallback_used=False,
        )
    )
    module = SystemMonitorModule(
        config=config,
        provider_registry=registry,
        store=store,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("show my monitors", model_override="codex-cli")

    assert "Configured monitor rules:" in result.response_text
    assert "High CPU" in result.response_text


@pytest.mark.anyio
async def test_system_monitor_module_removes_rule(tmp_path: Path) -> None:
    """Remove requests should delete an existing monitor by id or name."""

    config = load_config(tmp_path / "config.toml")
    store = MonitorsStore(tmp_path / "monitors.toml")
    await store.save_rules(
        [
            MonitorRule(
                rule_id="deadbeef",
                name="High CPU",
                metric="cpu_percent",
                operator="gt",
                threshold=85,
                message="CPU high",
                cooldown_seconds=300,
                enabled=True,
            )
        ]
    )
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"remove_monitor","arguments":{"identifier":"High CPU"}}',
            fallback_used=False,
        )
    )
    module = SystemMonitorModule(
        config=config,
        provider_registry=registry,
        store=store,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("remove the high cpu monitor", model_override="codex-cli")

    assert "Removed monitor 'High CPU'" in result.response_text
    assert await store.load_rules() == []


@pytest.mark.anyio
async def test_system_monitor_service_notifies_once_per_trigger_edge(tmp_path: Path) -> None:
    """Triggered rules should notify once until the metric clears and crosses again."""

    config = load_config(tmp_path / "config.toml")
    store = MonitorsStore(tmp_path / "monitors.toml")
    await store.save_rules(
        [
            MonitorRule(
                rule_id="deadbeef",
                name="High RAM",
                metric="memory_percent",
                operator="gt",
                threshold=90,
                message="Memory is high at {value}",
                cooldown_seconds=0,
                enabled=True,
            )
        ]
    )
    bridge = FakeBridge()
    service = SystemMonitorService(config=config, bridge=bridge, store=store, logger=logging.getLogger("test"))

    readings = iter(
        [
            {"memory_percent": 95.0},
            {"memory_percent": 96.0},
            {"memory_percent": 50.0},
            {"memory_percent": 97.0},
        ]
    )

    async def fake_collect_metrics() -> dict[str, float]:
        return next(readings)

    service._collect_metrics = fake_collect_metrics  # type: ignore[method-assign]

    await service.poll_once()
    await service.poll_once()
    await service.poll_once()
    await service.poll_once()

    assert len(bridge.notifications) == 2
    assert bridge.notifications[0][0] == "Nyx monitor: High RAM"


def test_system_monitor_module_matcher_is_conservative() -> None:
    """Only obvious alert/monitor prompts should route into Phase 17."""

    assert SystemMonitorModule.matches_request("alert me if memory usage exceeds 90 percent") is True
    assert SystemMonitorModule.matches_request("show my monitors") is True
    assert SystemMonitorModule.matches_request("watch cpu usage for me") is True
    assert SystemMonitorModule.matches_request("show tasks for nyx") is False
