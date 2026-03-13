"""Bridge exports for Nyx platform abstractions."""

from nyx.bridges.base import (
    BridgeCommandError,
    BridgeConfirmationRequiredError,
    BridgeNotImplementedError,
    BridgeSecurityError,
    SystemBridge,
    WindowInfo,
)
from nyx.bridges.factory import get_system_bridge
from nyx.bridges.hyprland import HyprlandBridge
from nyx.bridges.stub import StubBridge

__all__ = [
    "BridgeCommandError",
    "BridgeConfirmationRequiredError",
    "BridgeNotImplementedError",
    "BridgeSecurityError",
    "HyprlandBridge",
    "SystemBridge",
    "WindowInfo",
    "StubBridge",
    "get_system_bridge",
]
