"""Feature modules for Nyx."""

from nyx.modules.calendar import CalendarModule
from nyx.modules.cross_device_sync import CrossDeviceSyncModule
from nyx.modules.git_github import GitHubModule
from nyx.modules.macros import MacrosModule
from nyx.modules.memory import MemoryModule
from nyx.modules.notes import NotesModule
from nyx.modules.screen_context import ScreenContextModule
from nyx.modules.skills import SkillsModule
from nyx.modules.system_monitor import SystemMonitorModule
from nyx.modules.system_control import SystemControlModule
from nyx.modules.tasks import TasksModule
from nyx.modules.web_lookup import WebLookupModule

__all__ = [
    "CalendarModule",
    "CrossDeviceSyncModule",
    "GitHubModule",
    "MacrosModule",
    "MemoryModule",
    "NotesModule",
    "ScreenContextModule",
    "SkillsModule",
    "SystemMonitorModule",
    "SystemControlModule",
    "TasksModule",
    "WebLookupModule",
]
