"""Tests for the Phase 16 skills module and scheduler."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.bridges.stub import StubBridge
from nyx.config import load_config
from nyx.modules.skills import SkillsModule
from nyx.providers.base import ProviderQueryResult
from nyx.skills import SkillsScheduler
from nyx.skills.runtime import SkillDefinition


@dataclass
class FakeProviderRegistry:
    """Minimal registry stub that returns queued provider results."""

    results: list[ProviderQueryResult] = field(default_factory=list)
    seen_prompts: list[str] = field(default_factory=list)
    seen_contexts: list[dict[str, Any]] = field(default_factory=list)
    seen_preferred_provider_names: list[str | None] = field(default_factory=list)

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return the next configured provider result."""

        self.seen_prompts.append(prompt)
        self.seen_contexts.append(context)
        self.seen_preferred_provider_names.append(preferred_provider_name)
        assert self.results, "No fake provider results left."
        return self.results.pop(0)


def _skill_source(
    *,
    name: str,
    description: str,
    triggers: str,
    trigger_modes: str,
    body: str,
    schedule_seconds: int | None = None,
) -> str:
    """Return a simple test skill source file."""

    schedule_line = f"schedule_seconds: {schedule_seconds}\n" if schedule_seconds is not None else ""
    return (
        '"""\n'
        f"name: {name}\n"
        f"description: {description}\n"
        f"triggers: {triggers}\n"
        f"trigger_modes: {trigger_modes}\n"
        f"{schedule_line}"
        '"""\n\n'
        "def run(context):\n"
        f"    {body}\n"
    )


@pytest.mark.anyio
async def test_skills_module_runs_explicit_skill(tmp_path: Path) -> None:
    """Explicit skill invocations should run by name without provider planning."""

    config = load_config(tmp_path / "config.toml")
    skills_dir = config.config_path.parent / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "desk-summary.py").write_text(
        _skill_source(
            name="Desk Summary",
            description="Summarize the current desk.",
            triggers="desk summary, summary desk",
            trigger_modes="explicit",
            body='return "desk summary skill"',
        ),
        encoding="utf-8",
    )
    module = SkillsModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=FakeProviderRegistry(),
        logger=logging.getLogger("test"),
    )

    result = await module.maybe_handle("run the skill Desk Summary", model_override="codex-cli")

    assert result is not None
    assert result.response_text == "desk summary skill"
    assert result.used_model == "codex-cli"


@pytest.mark.anyio
async def test_skills_module_runs_keyword_skill_without_provider(tmp_path: Path) -> None:
    """Keyword-triggered skills should run directly on phrase matches."""

    config = load_config(tmp_path / "config.toml")
    skills_dir = config.config_path.parent / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "battery-helper.py").write_text(
        _skill_source(
            name="Battery Helper",
            description="Handle battery questions.",
            triggers="battery status, battery helper",
            trigger_modes="keyword",
            body='return "battery skill"',
        ),
        encoding="utf-8",
    )
    registry = FakeProviderRegistry()
    module = SkillsModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.maybe_handle("please show my battery status", model_override=None)

    assert result is not None
    assert result.response_text == "battery skill"
    assert registry.seen_prompts == []


@pytest.mark.anyio
async def test_skills_module_uses_ai_intent_planner(tmp_path: Path) -> None:
    """AI-intent skills should be selected through the provider planner."""

    config = load_config(tmp_path / "config.toml")
    skills_dir = config.config_path.parent / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "repo-helper.py").write_text(
        _skill_source(
            name="Repo Helper",
            description="Help with repository housekeeping.",
            triggers="repo helper, repository helper",
            trigger_modes="ai_intent",
            body='return f"repo skill: {context.arguments}"',
        ),
        encoding="utf-8",
    )
    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"run_skill","arguments":{"name":"Repo Helper","arguments":"review stale branches"}}',
                fallback_used=False,
            )
        ]
    )
    module = SkillsModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.maybe_handle("please clean up the repository state", model_override="codex-cli")

    assert result is not None
    assert result.response_text == "repo skill: review stale branches"
    assert registry.seen_preferred_provider_names == ["codex-cli"]


@pytest.mark.anyio
async def test_skills_module_respects_disabled_list(tmp_path: Path) -> None:
    """Disabled skills should not be discovered or executed."""

    config = load_config(tmp_path / "config.toml")
    config.skills.disabled = ["Desk Summary"]
    skills_dir = config.config_path.parent / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "desk-summary.py").write_text(
        _skill_source(
            name="Desk Summary",
            description="Summarize the current desk.",
            triggers="desk summary",
            trigger_modes="explicit, keyword",
            body='return "desk summary skill"',
        ),
        encoding="utf-8",
    )
    module = SkillsModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=FakeProviderRegistry(),
        logger=logging.getLogger("test"),
    )

    result = await module.maybe_handle("run the skill Desk Summary", model_override=None)

    assert result is None or "could not find an enabled skill" in result.response_text


@pytest.mark.anyio
async def test_skills_scheduler_runs_scheduled_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Scheduled skills should run in the daemon scheduler loop."""

    config = load_config(tmp_path / "config.toml")
    calls: list[str] = []

    async def fake_discover_skills(*args, **kwargs):
        del args, kwargs
        return [
            SkillDefinition(
                name="Heartbeat",
                description="Emit a heartbeat.",
                triggers=["heartbeat"],
                trigger_modes=["scheduled"],
                file_path=tmp_path / "skills" / "heartbeat.py",
                schedule_seconds=1,
            )
        ]

    async def fake_execute_skill(skill, context):
        del context
        calls.append(skill.name)
        return "heartbeat ok"

    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float) -> None:
        await original_sleep(0)

    monkeypatch.setattr("nyx.skills.scheduler.discover_skills", fake_discover_skills)
    monkeypatch.setattr("nyx.skills.scheduler.execute_skill", fake_execute_skill)
    monkeypatch.setattr("nyx.skills.scheduler.asyncio.sleep", fast_sleep)

    scheduler = SkillsScheduler(config=config, bridge=StubBridge("Linux"), logger=logging.getLogger("test"))
    await scheduler.start()
    await asyncio.sleep(0)
    await scheduler.stop()

    assert calls
