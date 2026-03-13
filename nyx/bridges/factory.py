"""Factory for selecting the active Nyx system bridge.

Linux now uses the real Hyprland bridge while unsupported future targets remain
stubbed until their platform implementations land.
"""

from __future__ import annotations

import logging
import platform

from nyx.bridges.base import SystemBridge
from nyx.bridges.hyprland import HyprlandBridge
from nyx.config import NyxConfig
from nyx.bridges.stub import StubBridge


def get_system_bridge(
    config: NyxConfig,
    logger: logging.Logger | None = None,
) -> SystemBridge:
    """Create the platform bridge for the current operating system.

    Args:
        config: Loaded Nyx configuration provided to bridge implementations.
        logger: Optional logger passed to bridge implementations for diagnostics.

    Returns:
        A ``SystemBridge`` instance appropriate for the current platform.

    Raises:
        NotImplementedError: The current platform is outside the documented
            Linux and future Windows target set.
    """

    system_name = platform.system()
    if system_name == "Linux":
        return HyprlandBridge(config=config, logger=logger)
    if system_name == "Windows":
        return StubBridge("Windows", logger=logger)
    raise NotImplementedError(f"Platform {system_name} not supported yet")
