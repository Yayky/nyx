"""Tests for the Phase 7 notes module."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.config import load_config
from nyx.modules.notes import NotesModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeProviderRegistry:
    """Minimal provider registry stub used to test notes planning."""

    results: list[ProviderQueryResult]
    seen_prompts: list[str] | None = None
    seen_contexts: list[dict[str, Any]] | None = None
    seen_preferred_provider_names: list[str | None] | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return the next configured planner result."""

        if self.seen_prompts is None:
            self.seen_prompts = []
        if self.seen_contexts is None:
            self.seen_contexts = []
        if self.seen_preferred_provider_names is None:
            self.seen_preferred_provider_names = []

        self.seen_prompts.append(prompt)
        self.seen_contexts.append(context)
        self.seen_preferred_provider_names.append(preferred_provider_name)
        return self.results.pop(0)


@pytest.mark.anyio
async def test_notes_capture_appends_to_inbox(tmp_path: Path) -> None:
    """A note capture should persist a structured entry in the inbox."""

    config_path = tmp_path / "missing.toml"
    config = load_config(config_path)
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"

    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"append_inbox","arguments":{"content":"Buy oat milk"}}',
                fallback_used=False,
            )
        ]
    )
    module = NotesModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    result = await module.handle("note: buy oat milk", model_override="codex-cli")

    inbox_text = (config.notes.notes_dir / config.notes.inbox_file).read_text(encoding="utf-8")
    assert result.response_text == f"Saved note to {config.notes.notes_dir / config.notes.inbox_file}."
    assert "Buy oat milk" in inbox_text
    assert "status: inbox" in inbox_text
    assert registry.seen_preferred_provider_names == ["codex-cli"]


@pytest.mark.anyio
async def test_notes_capture_routes_to_existing_project_when_auto_sort_enabled(tmp_path: Path) -> None:
    """Auto-sort should route captured notes into an existing project notes file."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    (config.notes.projects_dir / "alpha").mkdir(parents=True)

    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="ollama-local",
                provider_type="ollama",
                model_name="qwen2.5:7b",
                text='{"operation":"route_to_project","arguments":{"project":"alpha","content":"Refactor auth flow"}}',
                fallback_used=True,
                degraded=True,
                degraded_reason="local_only",
            )
        ]
    )
    module = NotesModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    result = await module.handle("note this for alpha: refactor auth flow")

    inbox_text = (config.notes.notes_dir / config.notes.inbox_file).read_text(encoding="utf-8")
    project_notes = (config.notes.projects_dir / "alpha" / "notes.md").read_text(encoding="utf-8")

    assert result.degraded is True
    assert "routed it to" in result.response_text
    assert "status: routed" in inbox_text
    assert "project: alpha" in inbox_text
    assert "Refactor auth flow" in project_notes


@pytest.mark.anyio
async def test_notes_sort_inbox_routes_pending_entries(tmp_path: Path) -> None:
    """Explicit inbox sorting should route matching pending entries."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    (config.notes.projects_dir / "beta").mkdir(parents=True)

    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"append_inbox","arguments":{"content":"Fix beta deploy docs"}}',
                fallback_used=False,
            ),
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"route_to_project","arguments":{"project":"beta","content":"Fix beta deploy docs"}}',
                fallback_used=False,
            ),
        ]
    )
    module = NotesModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    await module.handle("note: fix beta deploy docs")
    result = await module.handle("sort inbox")

    inbox_text = (config.notes.notes_dir / config.notes.inbox_file).read_text(encoding="utf-8")
    project_notes = (config.notes.projects_dir / "beta" / "notes.md").read_text(encoding="utf-8")

    assert result.response_text == "Routed 1 inbox entry."
    assert "status: routed" in inbox_text
    assert "Fix beta deploy docs" in project_notes


def test_notes_matcher_is_conservative() -> None:
    """The notes router matcher should catch obvious capture and inbox commands."""

    assert NotesModule.matches_request("note: buy milk") is True
    assert NotesModule.matches_request("sort inbox") is True
    assert NotesModule.matches_request("search inbox for coffee filters") is False
    assert NotesModule.matches_request("explain milk proteins") is False
