"""Safe entrypoint for the Nyx GTK launcher.

This module avoids importing GTK before ``gtk4-layer-shell`` has a chance to be
preloaded. The preload is required on some systems so the library initializes
before Wayland client symbols are resolved.
"""

from __future__ import annotations

import ctypes.util
import os
from pathlib import Path
import sys
from typing import Final

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig
from nyx.daemon import NyxDaemon

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

    from nyx.ui.launcher import run_launcher as run_launcher_impl

    return run_launcher_impl(
        config=config,
        daemon=daemon,
        bridge=bridge,
        logger=logger,
        initial_prompt=initial_prompt,
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
