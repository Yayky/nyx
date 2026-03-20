"""Safe entrypoint for the Nyx GTK launcher.

This module avoids importing GTK before ``gtk4-layer-shell`` has a chance to be
preloaded. The preload is required on some systems so the library initializes
before Wayland client symbols are resolved.
"""

from __future__ import annotations

import asyncio
import ctypes.util
import os
from pathlib import Path
import sys
from typing import Final

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig
from nyx.daemon import NyxDaemon
from nyx.ui.monitors import MonitorSelectionState

PRELOAD_ENV_FLAG: Final[str] = "NYX_LAYER_SHELL_PRELOADED"


def run_launcher(
    config: NyxConfig,
    daemon: NyxDaemon,
    bridge: SystemBridge,
    logger,
    initial_prompt: str = "",
) -> int:
    """Run the GTK launcher, preloading ``gtk4-layer-shell`` when needed."""

    _ensure_layer_shell_preload()
    monitor_state = _load_monitor_selection_state(config, bridge, logger)
    try:
        run_launcher_impl = _import_launcher_impl()
    except ModuleNotFoundError as exc:
        if exc.name == "gi":
            raise RuntimeError(_missing_gtk_bindings_message()) from exc
        raise

    return run_launcher_impl(
        config=config,
        daemon=daemon,
        bridge=bridge,
        logger=logger,
        monitor_state=monitor_state,
        initial_prompt=initial_prompt,
    )


def run_workspace(
    *,
    config: NyxConfig,
    logger,
    initial_section: str = "workspace",
) -> int:
    """Run the standalone GTK workspace window."""

    try:
        run_workspace_impl = _import_workspace_impl()
    except ModuleNotFoundError as exc:
        if exc.name == "gi":
            raise RuntimeError(_missing_gtk_bindings_message()) from exc
        raise
    return run_workspace_impl(
        config=config,
        logger=logger,
        initial_section=initial_section,
    )


def _import_launcher_impl():
    """Import the GTK launcher lazily after preload setup."""

    from nyx.ui.launcher import run_launcher as run_launcher_impl

    return run_launcher_impl


def _import_workspace_impl():
    """Import the GTK workspace lazily when requested."""

    from nyx.ui.workspace import run_workspace as run_workspace_impl

    return run_workspace_impl


def _load_monitor_selection_state(
    config: NyxConfig,
    bridge: SystemBridge,
    logger,
) -> MonitorSelectionState:
    """Preload monitor-selection state from the bridge before GTK starts."""

    if config.ui.overlay_monitor != "focused":
        return MonitorSelectionState()

    try:
        focused_monitor = asyncio.run(bridge.get_focused_monitor())
    except Exception as exc:
        logger.warning("Unable to resolve focused monitor for launcher placement: %s", exc)
        return MonitorSelectionState()

    if focused_monitor is None or not focused_monitor.name:
        return MonitorSelectionState()
    return MonitorSelectionState(focused_monitor_name=focused_monitor.name)


def _missing_gtk_bindings_message() -> str:
    """Return an actionable launcher error for missing PyGObject bindings."""

    return (
        "GTK launcher dependencies are missing from the active Python environment. "
        "Install the system GTK bindings first, for example on Arch Linux: "
        "`sudo pacman -S python-gobject gtk4 gtk4-layer-shell`. "
        "If you are using a virtual environment, recreate it with "
        "`python3 -m venv --system-site-packages .venv` so the system `gi` package is visible."
    )


def _ensure_layer_shell_preload() -> None:
    """Re-exec the process with ``LD_PRELOAD`` when layer-shell is not preloaded."""

    if os.name != "posix":
        return
    if os.environ.get(PRELOAD_ENV_FLAG) == "1":
        return

    library_path = _locate_layer_shell_library()
    if library_path is None:
        return

    current_preload = os.environ.get("LD_PRELOAD", "")
    preloaded_entries = [entry for entry in current_preload.split(":") if entry]
    if library_path in preloaded_entries:
        return

    env = dict(os.environ)
    env[PRELOAD_ENV_FLAG] = "1"
    env["LD_PRELOAD"] = ":".join([library_path, *preloaded_entries])
    argv = list(getattr(sys, "orig_argv", [])) or [sys.executable, *sys.argv]
    os.execvpe(argv[0], argv, env)


def _locate_layer_shell_library() -> str | None:
    """Find the shared library path for ``gtk4-layer-shell``."""

    candidates = [
        Path("/usr/lib/libgtk4-layer-shell.so"),
        Path("/usr/lib/libgtk4-layer-shell.so.0"),
    ]
    found = ctypes.util.find_library("gtk4-layer-shell")
    if found:
        candidates.append(Path(found))
        candidates.append(Path("/usr/lib") / found)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None
