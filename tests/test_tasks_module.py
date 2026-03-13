"""Tests for the Phase 13 tasks module."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.config import load_config
from nyx.modules.tasks import TasksModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeProviderRegistry:
    """Minimal registry stub for provider-planned task requests."""

    result: ProviderQueryResult
    seen_prompt: str | None = None
    seen_context: dict[str, Any] | None = None
    seen_preferred_provider_name: str | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return a deterministic provider planning result."""

        self.seen_prompt = prompt
        self.seen_context = context
        self.seen_preferred_provider_name = preferred_provider_name
        return self.result


@pytest.mark.anyio
async def test_tasks_module_adds_task_to_project_file(tmp_path: Path) -> None:
    """Add-task requests should append unchecked markdown tasks."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    (config.notes.projects_dir / "nyx").mkdir(parents=True)
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"add_task","arguments":{"project":"nyx","content":"Implement task module"}}',
            fallback_used=False,
        )
    )
    module = TasksModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    result = await module.handle("add a task for nyx to implement task module", model_override="codex-cli")

    tasks_path = config.notes.projects_dir / "nyx" / "tasks.md"
    assert result.operation == "add_task"
    assert tasks_path.read_text(encoding="utf-8").strip() == "- [ ] Implement task module"


@pytest.mark.anyio
async def test_tasks_module_lists_open_tasks(tmp_path: Path) -> None:
    """List-task requests should render open tasks from the project file."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    project_dir = config.notes.projects_dir / "nyx"
    project_dir.mkdir(parents=True)
    (project_dir / "tasks.md").write_text(
        "- [ ] Implement task module\n- [x] Ship launcher polish\n",
        encoding="utf-8",
    )
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"list_tasks","arguments":{"project":"nyx","include_completed":false}}',
            fallback_used=False,
        )
    )
    module = TasksModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    result = await module.handle("show open tasks for nyx", model_override="codex-cli")

    assert "Open tasks for nyx:" in result.response_text
    assert "- [ ] Implement task module" in result.response_text
    assert "Ship launcher polish" not in result.response_text


@pytest.mark.anyio
async def test_tasks_module_completes_first_matching_open_task(tmp_path: Path) -> None:
    """Complete-task requests should mark the first matching open checkbox done."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    project_dir = config.notes.projects_dir / "nyx"
    project_dir.mkdir(parents=True)
    tasks_path = project_dir / "tasks.md"
    tasks_path.write_text(
        "- [ ] Implement task module\n- [ ] Write docs\n",
        encoding="utf-8",
    )
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"complete_task","arguments":{"project":"nyx","task":"implement task"}}',
            fallback_used=False,
        )
    )
    module = TasksModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    result = await module.handle("complete the implement task for nyx", model_override="codex-cli")

    assert "Completed task in nyx: Implement task module" in result.response_text
    assert tasks_path.read_text(encoding="utf-8").splitlines()[0] == "- [x] Implement task module"


@pytest.mark.anyio
async def test_tasks_module_reports_unknown_project(tmp_path: Path) -> None:
    """Unknown project references should fail cleanly without creating files."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"add_task","arguments":{"project":"ghost","content":"Do thing"}}',
            fallback_used=False,
        )
    )
    module = TasksModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    result = await module.handle("add a task for ghost to do thing", model_override="codex-cli")

    assert "could not find a project named 'ghost'" in result.response_text
    assert not (config.notes.projects_dir / "ghost").exists()


def test_tasks_module_matcher_is_conservative() -> None:
    """Only task-like prompts should route into the Phase 13 module."""

    assert TasksModule.matches_request("add a task for nyx to write tests") is True
    assert TasksModule.matches_request("show tasks for nyx") is True
    assert TasksModule.matches_request("complete the release task for nyx") is True
    assert TasksModule.matches_request("write release notes for nyx") is False
