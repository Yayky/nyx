"""Background scheduler for Phase 16 Nyx skills."""

from __future__ import annotations

import asyncio
import logging

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig
from nyx.skills.runtime import SkillContext, SkillDefinition, discover_skills, execute_skill


class SkillsScheduler:
    """Run scheduled skills inside the Nyx daemon lifecycle."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the scheduler with config, bridge, and logger dependencies."""

        self.config = config
        self.bridge = bridge
        self.logger = logger or logging.getLogger("nyx.skills.scheduler")
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Discover scheduled skills and start periodic execution tasks."""

        skills = await discover_skills(
            self.config.config_path.parent / "skills",
            disabled_names=set(self.config.skills.disabled),
        )
        scheduled_skills = [
            skill for skill in skills if "scheduled" in skill.trigger_modes and skill.schedule_seconds
        ]
        for skill in scheduled_skills:
            self._tasks.append(asyncio.create_task(self._run_skill_loop(skill)))

    async def stop(self) -> None:
        """Cancel all background scheduled-skill tasks."""

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_skill_loop(self, skill: SkillDefinition) -> None:
        """Run one scheduled skill on its configured interval."""

        assert skill.schedule_seconds is not None
        while True:
            try:
                result = await execute_skill(
                    skill,
                    SkillContext(
                        config=self.config,
                        bridge=self.bridge,
                        logger=self.logger,
                        request_text=None,
                        skill=skill,
                        trigger_mode="scheduled",
                        arguments=None,
                    ),
                )
                if result:
                    self.logger.info("Scheduled skill '%s' completed: %s", skill.name, result)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Scheduled skill '%s' failed.", skill.name)
            await asyncio.sleep(skill.schedule_seconds)
