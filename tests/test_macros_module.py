"""Tests for the Phase 15 macros module."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.bridges.stub import StubBridge
from nyx.config import load_config
from nyx.modules.macros import MacrosModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeProviderRegistry:
    """Minimal registry stub that returns queued provider results."""

    results: list[ProviderQueryResult]
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


def _macro_source(*, name: str, scope: str, description: str, body: str) -> str:
    """Return a simple test macro source file."""

    return (
        f'"""\n'
        f"name: {name}\n"
        f"triggers: {name}, test {name}\n"
        f"scope: {scope}\n"
        f"description: {description}\n"
        f'"""\n\n'
        "def run(context):\n"
        f"    {body}\n"
    )


@pytest.mark.anyio
async def test_macros_module_creates_global_macro(tmp_path: Path) -> None:
    """Create requests should generate and persist a global macro file."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"create_macro","arguments":{"name":"Desk summary","scope":"global","project":null,"description":"Summarize the current desktop state.","triggers":["desk summary","desktop summary"]}}',
                fallback_used=False,
            ),
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text=_macro_source(
                    name="Desk summary",
                    scope="global",
                    description="Summarize the current desktop state.",
                    body='return "desktop summary"',
                ),
                fallback_used=False,
            ),
        ]
    )
    module = MacrosModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("create a global macro called desk summary", model_override="codex-cli")

    macro_path = config.config_path.parent / "macros" / "desk-summary.py"
    assert result.operation == "create_macro"
    assert macro_path.exists()
    assert "Created global macro 'Desk summary'" in result.response_text
    assert "triggers: Desk summary, test Desk summary" in macro_path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_macros_module_strips_generator_preamble_before_persisting(tmp_path: Path) -> None:
    """Create requests should discard prose before the actual Python module."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"create_macro","arguments":{"name":"Desk summary","scope":"global","project":null,"description":"Summarize the current desktop state.","triggers":["desk summary"]}}',
                fallback_used=False,
            ),
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text=(
                    "I am checking the repo for macro conventions first.\n\n"
                    + _macro_source(
                        name="Desk summary",
                        scope="global",
                        description="Summarize the current desktop state.",
                        body='return "desktop summary"',
                    )
                ),
                fallback_used=False,
            ),
        ]
    )
    module = MacrosModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("create a global macro called desk summary", model_override="codex-cli")

    macro_path = config.config_path.parent / "macros" / "desk-summary.py"
    assert "Created global macro 'Desk summary'" in result.response_text
    assert macro_path.read_text(encoding="utf-8").startswith('"""')


@pytest.mark.anyio
async def test_macros_module_lists_project_and_global_macros(tmp_path: Path) -> None:
    """List requests should render both global and project-linked macros."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    global_dir = config.config_path.parent / "macros"
    project_dir = config.notes.projects_dir / "nyx" / "macros"
    global_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (global_dir / "desk-summary.py").write_text(
        _macro_source(
            name="Desk summary",
            scope="global",
            description="Summarize the desktop.",
            body='return "desktop summary"',
        ),
        encoding="utf-8",
    )
    (project_dir / "ship-notes.py").write_text(
        _macro_source(
            name="Ship notes",
            scope="project",
            description="Draft release notes for Nyx.",
            body='return "release notes"',
        ),
        encoding="utf-8",
    )
    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"list_macros","arguments":{"scope":"all","project":null}}',
                fallback_used=False,
            )
        ]
    )
    module = MacrosModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("list macros", model_override="codex-cli")

    assert "Desk summary [global]" in result.response_text
    assert "Ship notes [project/nyx]" in result.response_text


@pytest.mark.anyio
async def test_macros_module_runs_existing_macro(tmp_path: Path) -> None:
    """Run requests should execute the macro's ``run(context)`` entrypoint."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    global_dir = config.config_path.parent / "macros"
    global_dir.mkdir(parents=True)
    (global_dir / "desk-summary.py").write_text(
        _macro_source(
            name="Desk summary",
            scope="global",
            description="Summarize the desktop.",
            body='return f"macro saw: {context.request_text}"',
        ),
        encoding="utf-8",
    )
    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"run_macro","arguments":{"name":"Desk summary","scope":"global","project":null,"arguments":null}}',
                fallback_used=False,
            )
        ]
    )
    module = MacrosModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("run the desk summary macro", model_override="codex-cli")

    assert result.response_text == "macro saw: run the desk summary macro"


@pytest.mark.anyio
async def test_macros_module_shows_existing_macro_source(tmp_path: Path) -> None:
    """Show requests should return the stored macro source text."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    global_dir = config.config_path.parent / "macros"
    global_dir.mkdir(parents=True)
    source = _macro_source(
        name="Desk summary",
        scope="global",
        description="Summarize the desktop.",
        body='return "desktop summary"',
    )
    (global_dir / "desk-summary.py").write_text(source, encoding="utf-8")
    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"show_macro","arguments":{"name":"Desk summary","scope":"global","project":null}}',
                fallback_used=False,
            )
        ]
    )
    module = MacrosModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("show macro desk summary", model_override="codex-cli")

    assert "Macro 'Desk summary'" in result.response_text
    assert 'description: Summarize the desktop.' in result.response_text


def test_macros_module_matcher_is_conservative() -> None:
    """Only explicit macro-like prompts should route into the Phase 15 module."""

    assert MacrosModule.matches_request("create a macro that summarizes my desktop") is True
    assert MacrosModule.matches_request("list macros for nyx") is True
    assert MacrosModule.matches_request("run the build automation macro") is True
    assert MacrosModule.matches_request("show tasks for nyx") is False


@pytest.mark.anyio
async def test_macros_module_ignores_invalid_existing_macro_files(tmp_path: Path) -> None:
    """Invalid saved macro files should not break macro discovery/listing."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    global_dir = config.config_path.parent / "macros"
    global_dir.mkdir(parents=True)
    (global_dir / "broken.py").write_text("I’m broken\n", encoding="utf-8")
    (global_dir / "desk-summary.py").write_text(
        _macro_source(
            name="Desk summary",
            scope="global",
            description="Summarize the desktop.",
            body='return "desktop summary"',
        ),
        encoding="utf-8",
    )
    registry = FakeProviderRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"list_macros","arguments":{"scope":"all","project":null}}',
                fallback_used=False,
            )
        ]
    )
    module = MacrosModule(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("list macros", model_override="codex-cli")

    assert "Desk summary [global]" in result.response_text
    assert "broken" not in result.response_text
