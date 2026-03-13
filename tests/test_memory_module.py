"""Tests for the Phase 10 persistent-memory module."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.config import load_config
from nyx.modules.memory import MemoryModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeProviderRegistry:
    """Minimal provider registry stub for memory-planner tests."""

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
        """Return the configured memory-planner result."""

        self.seen_prompt = prompt
        self.seen_context = context
        self.seen_preferred_provider_name = preferred_provider_name
        return self.result


@pytest.mark.anyio
async def test_memory_module_creates_global_proposal(tmp_path: Path) -> None:
    """Remember-style prompts should create a pending global memory proposal."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"propose_global","arguments":{"content":"User prefers concise answers."}}',
            fallback_used=False,
        )
    )
    module = MemoryModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    result = await module.handle("remember that I prefer concise answers", model_override="codex-cli")

    proposals_path = config.config_path.parent / "memory_proposals.json"
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    assert result.used_model == "codex-cli"
    assert "Created memory proposal" in result.response_text
    assert proposals[0]["target"] == "global"
    assert proposals[0]["content"] == "User prefers concise answers."


@pytest.mark.anyio
async def test_memory_module_applies_project_proposal_to_context_file(tmp_path: Path) -> None:
    """Applying a project proposal should append the content to ``context.md``."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    (config.notes.projects_dir / "nyx").mkdir(parents=True)
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"propose_project","arguments":{"project":"nyx","content":"Nyx targets Hyprland and Wayland first."}}',
            fallback_used=False,
        )
    )
    module = MemoryModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    create_result = await module.handle("remember for nyx that it targets Hyprland first")
    proposal_id = create_result.response_text.split()[3]
    apply_result = await module.handle(f"apply memory proposal {proposal_id}")

    context_path = config.notes.projects_dir / "nyx" / "context.md"
    proposals = json.loads((config.config_path.parent / "memory_proposals.json").read_text(encoding="utf-8"))

    assert "Applied memory proposal" in apply_result.response_text
    assert "- Nyx targets Hyprland and Wayland first." in context_path.read_text(encoding="utf-8")
    assert proposals[0]["status"] == "applied"


@pytest.mark.anyio
async def test_memory_module_can_show_and_skip_proposals(tmp_path: Path) -> None:
    """Listing, skipping, and showing memory should work without provider calls."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"propose_global","arguments":{"content":"User uses Codex CLI daily."}}',
            fallback_used=False,
        )
    )
    module = MemoryModule(config=config, provider_registry=registry, logger=logging.getLogger("test"))

    create_result = await module.handle("remember that I use Codex CLI daily")
    proposal_id = create_result.response_text.split()[3]
    list_result = await module.handle("list memory proposals")
    skip_result = await module.handle(f"skip memory proposal {proposal_id}")
    show_result = await module.handle("show memory")

    assert proposal_id in list_result.response_text
    assert "Skipped memory proposal" in skip_result.response_text
    assert show_result.response_text == "Global memory is empty."


def test_memory_matcher_is_conservative() -> None:
    """Only explicit memory-management prompts should route into Phase 10 memory."""

    assert MemoryModule.matches_request("remember that I prefer concise answers") is True
    assert MemoryModule.matches_request("apply memory proposal deadbeef") is True
    assert MemoryModule.matches_request("show project context for nyx") is True
    assert MemoryModule.matches_request("explain memory consistency models") is False
