"""Shared monitor-selection helpers for Nyx GTK overlays.

Phase 22 centralizes monitor selection so the launcher and panel honor the same
config semantics: use the focused monitor by default, allow explicit named
outputs, and keep numeric/primary fallbacks for simple setups.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(slots=True)
class MonitorSelectionState:
    """Precomputed monitor-selection state passed from the bridge layer."""

    focused_monitor_name: str | None = None


def resolve_overlay_monitor(display, selection: str, state: MonitorSelectionState):
    """Resolve one GTK monitor object from config and precomputed monitor state."""

    if display is None:
        return None

    monitors = _gtk_monitors(display)
    if not monitors:
        return None

    normalized = selection.strip()
    if normalized == "focused":
        if state.focused_monitor_name:
            focused_match = _find_monitor_by_name(monitors, state.focused_monitor_name)
            if focused_match is not None:
                return focused_match
        return None

    if normalized == "primary":
        return monitors[0]

    if normalized.isdigit():
        index = max(0, int(normalized) - 1)
        if index < len(monitors):
            return monitors[index]
        return None

    if normalized:
        return _find_monitor_by_name(monitors, normalized)
    return None


def _gtk_monitors(display) -> list:
    """Materialize the GTK monitor model into a simple list."""

    model = display.get_monitors()
    count = model.get_n_items()
    return [model.get_item(index) for index in range(count) if model.get_item(index) is not None]


def _find_monitor_by_name(monitors: Iterable, selection: str):
    """Find a GTK monitor by connector, description, or model name."""

    target = selection.casefold()
    for monitor in monitors:
        values = [
            _safe_casefold(getattr(monitor, "get_connector", None)),
            _safe_casefold(getattr(monitor, "get_description", None)),
            _safe_casefold(getattr(monitor, "get_model", None)),
        ]
        if target in {value for value in values if value}:
            return monitor
    return None


def _safe_casefold(getter) -> str | None:
    """Call a monitor getter and normalize its string value when present."""

    if getter is None:
        return None
    value = getter()
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.casefold()
