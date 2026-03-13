"""Feature modules for Nyx."""

from nyx.modules.git_github import GitHubModule
from nyx.modules.memory import MemoryModule
from nyx.modules.notes import NotesModule
from nyx.modules.screen_context import ScreenContextModule
from nyx.modules.system_control import SystemControlModule

__all__ = [
    "GitHubModule",
    "MemoryModule",
    "NotesModule",
    "ScreenContextModule",
    "SystemControlModule",
]
