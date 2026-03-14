"""Tests for shared GTK overlay monitor selection helpers."""

from __future__ import annotations

from dataclasses import dataclass

from nyx.ui.monitors import MonitorSelectionState, resolve_overlay_monitor


@dataclass
class FakeMonitor:
    """Small duck-typed stand-in for ``Gdk.Monitor``."""

    connector: str
    description: str
    model: str

    def get_connector(self) -> str:
        """Return the simulated monitor connector name."""

        return self.connector

    def get_description(self) -> str:
        """Return the simulated monitor description."""

        return self.description

    def get_model(self) -> str:
        """Return the simulated monitor model string."""

        return self.model


class FakeMonitorModel:
    """Simple list-model stand-in for ``Gdk.Display.get_monitors()``."""

    def __init__(self, monitors: list[FakeMonitor]) -> None:
        """Store the backing monitor list."""

        self._monitors = monitors

    def get_n_items(self) -> int:
        """Return the number of fake monitors."""

        return len(self._monitors)

    def get_item(self, index: int):
        """Return one monitor by index."""

        return self._monitors[index]


@dataclass
class FakeDisplay:
    """Minimal display stub exposing a monitor list model."""

    monitors: list[FakeMonitor]

    def get_monitors(self) -> FakeMonitorModel:
        """Return the fake monitor model."""

        return FakeMonitorModel(self.monitors)


def test_resolve_overlay_monitor_uses_focused_monitor_name() -> None:
    """Focused selection should map the bridge-reported connector to GTK monitors."""

    left = FakeMonitor("eDP-2", "Laptop Panel", "NE156FHM")
    right = FakeMonitor("HDMI-A-1", "Dell 27", "U2722")
    display = FakeDisplay([left, right])

    monitor = resolve_overlay_monitor(
        display,
        "focused",
        MonitorSelectionState(focused_monitor_name="HDMI-A-1"),
    )

    assert monitor is right


def test_resolve_overlay_monitor_supports_named_connectors_and_descriptions() -> None:
    """Named selection should match connector and description values case-insensitively."""

    left = FakeMonitor("eDP-2", "Laptop Panel", "NE156FHM")
    right = FakeMonitor("HDMI-A-1", "Dell 27", "U2722")
    display = FakeDisplay([left, right])

    assert resolve_overlay_monitor(display, "eDP-2", MonitorSelectionState()) is left
    assert resolve_overlay_monitor(display, "dell 27", MonitorSelectionState()) is right


def test_resolve_overlay_monitor_supports_primary_and_numeric_selection() -> None:
    """Primary and one-based numeric monitor selection should remain supported."""

    left = FakeMonitor("eDP-2", "Laptop Panel", "NE156FHM")
    right = FakeMonitor("HDMI-A-1", "Dell 27", "U2722")
    display = FakeDisplay([left, right])

    assert resolve_overlay_monitor(display, "primary", MonitorSelectionState()) is left
    assert resolve_overlay_monitor(display, "2", MonitorSelectionState()) is right
    assert resolve_overlay_monitor(display, "3", MonitorSelectionState()) is None
