"""Proactive system monitor polling service for Nyx."""

from __future__ import annotations

import asyncio
import logging
import time

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig
from nyx.monitors.store import MonitorRule, MonitorsStore


class SystemMonitorService:
    """Evaluate persisted monitor rules on a background asyncio loop."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        store: MonitorsStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the monitor service with config, bridge, and store."""

        self.config = config
        self.bridge = bridge
        self.logger = logger or logging.getLogger("nyx.monitors.service")
        self.store = store or MonitorsStore(config.config_path.parent / "monitors.toml")
        self._task: asyncio.Task[None] | None = None
        self._active_rule_ids: set[str] = set()
        self._last_triggered_at: dict[str, float] = {}

    async def start(self) -> None:
        """Start the background polling task when not already running."""

        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the background polling task."""

        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def poll_once(self) -> None:
        """Evaluate all current rules once and emit notifications as needed."""

        rules = [rule for rule in await self.store.load_rules() if rule.enabled]
        if not rules:
            return

        metrics = await self._collect_metrics()
        now = time.monotonic()
        for rule in rules:
            metric_value = metrics.get(rule.metric)
            if metric_value is None:
                continue
            triggered = _evaluate_rule(rule, metric_value)
            if triggered:
                last_triggered = self._last_triggered_at.get(rule.rule_id, 0.0)
                cooldown_ok = (now - last_triggered) >= rule.cooldown_seconds
                if rule.rule_id not in self._active_rule_ids and cooldown_ok:
                    self._last_triggered_at[rule.rule_id] = now
                    self._active_rule_ids.add(rule.rule_id)
                    body = _render_message(rule, metric_value)
                    await self.bridge.notify(f"Nyx monitor: {rule.name}", body)
                    self.logger.info(
                        "Monitor rule '%s' triggered with value=%s",
                        rule.rule_id,
                        metric_value,
                    )
            else:
                self._active_rule_ids.discard(rule.rule_id)

    async def _poll_loop(self) -> None:
        """Run the monitor polling loop until canceled."""

        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("System monitor polling failed.")
            await asyncio.sleep(self.config.monitors.poll_interval_seconds)

    async def _collect_metrics(self) -> dict[str, float]:
        """Collect the current psutil-backed metric values."""

        import psutil

        battery = psutil.sensors_battery()
        disk_percent = psutil.disk_usage("/").percent
        memory_percent = psutil.virtual_memory().percent
        cpu_percent = psutil.cpu_percent(interval=None)
        metrics: dict[str, float] = {
            "cpu_percent": float(cpu_percent),
            "memory_percent": float(memory_percent),
            "disk_percent": float(disk_percent),
        }
        if battery is not None and battery.percent is not None:
            metrics["battery_percent"] = float(battery.percent)
        return metrics


def _evaluate_rule(rule: MonitorRule, value: float) -> bool:
    """Return whether one current metric value triggers the rule."""

    if rule.operator == "gt":
        return value > rule.threshold
    if rule.operator == "lt":
        return value < rule.threshold
    raise ValueError(f"Unsupported monitor operator: {rule.operator!r}")


def _render_message(rule: MonitorRule, value: float) -> str:
    """Render one user-facing notification message for a triggered rule."""

    try:
        return rule.message.format(
            metric=rule.metric,
            value=f"{value:.1f}",
            threshold=f"{rule.threshold:.1f}",
            operator=rule.operator,
            name=rule.name,
        )
    except Exception:
        return (
            f"{rule.message} Current value: {value:.1f}. "
            f"Threshold: {rule.operator} {rule.threshold:.1f}."
        )
