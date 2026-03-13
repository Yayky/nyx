"""Tests for the Phase 1 intent router stub."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import pytest

from nyx.bridges.stub import StubBridge
from nyx.config import load_config
from nyx.intent_router import IntentRequest, IntentRouter
from nyx.providers.base import ProviderError, ProviderQueryResult


@dataclass
class FakeRegistry:
    """Small async provider registry stub for router tests."""

    result: ProviderQueryResult | None = None
    error: ProviderError | None = None
    seen_preferred_provider: str | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Record the selection request and return the configured result."""

        self.seen_preferred_provider = preferred_provider_name
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    async def query_with_image(
        self,
        prompt: str,
        image_path,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return the configured result for vision queries as well."""

        del image_path
        return await self.query(prompt, context, preferred_provider_name)


@dataclass
class FakeGitHubModule:
    """Minimal git/github module stub for router dispatch tests."""

    async def handle(self, request_text: str, model_override: str | None = None):
        """Return a deterministic git/github module result."""

        del request_text, model_override
        return type(
            "GitHubResult",
            (),
            {
                "response_text": "git summary",
                "used_model": "codex-cli",
                "model_name": None,
                "token_count": None,
                "degraded": False,
            },
        )()

    @staticmethod
    def matches_request(text: str) -> bool:
        """Match explicit git diff prompts."""

        return "git diff" in text


@dataclass
class FakeCalendarModule:
    """Minimal calendar module stub for router dispatch tests."""

    async def handle(self, request_text: str, model_override: str | None = None):
        """Return a deterministic calendar module result."""

        del request_text, model_override
        return type(
            "CalendarResult",
            (),
            {
                "response_text": "calendar summary",
                "used_model": "codex-cli",
                "model_name": None,
                "token_count": None,
                "degraded": False,
            },
        )()

    @staticmethod
    def matches_request(text: str) -> bool:
        """Match explicit calendar prompts."""

        return "calendar" in text or "agenda" in text


@dataclass
class FakeMacrosModule:
    """Minimal macros module stub for router dispatch tests."""

    async def handle(self, request_text: str, model_override: str | None = None):
        """Return a deterministic macros module result."""

        del request_text, model_override
        return type(
            "MacrosResult",
            (),
            {
                "response_text": "macro output",
                "used_model": "codex-cli",
                "model_name": None,
                "token_count": None,
                "degraded": False,
            },
        )()

    @staticmethod
    def matches_request(text: str) -> bool:
        """Match explicit macro prompts."""

        return "macro" in text


@pytest.mark.anyio
async def test_router_returns_provider_result(tmp_path) -> None:
    """The router should return text from the selected provider."""

    config = load_config(tmp_path / "missing.toml")
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="ollama-local",
            provider_type="ollama",
            model_name="qwen2.5:7b",
            text="provider answer",
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(IntentRequest(text="hello", model_override=None, yolo=False))

    assert result.intent == "unclassified"
    assert result.target_module is None
    assert result.degraded is False
    assert result.used_model == "ollama-local"
    assert result.response_text == "provider answer"


@pytest.mark.anyio
async def test_router_prefers_model_override(tmp_path) -> None:
    """The model override should select the requested provider."""

    config = load_config(tmp_path / "missing.toml")
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text="provider answer",
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        git_github_module=FakeGitHubModule(),
        logger=logging.getLogger("test"),
    )

    result = await router.route(IntentRequest(text="hello", model_override="codex-cli", yolo=False))

    assert result.used_model == "codex-cli"
    assert registry.seen_preferred_provider == "codex-cli"


@pytest.mark.anyio
async def test_router_returns_degraded_message_when_provider_fails(tmp_path) -> None:
    """Provider failures should become degraded user-facing responses."""

    config = load_config(tmp_path / "missing.toml")
    registry = FakeRegistry(error=ProviderError("all configured providers failed"))
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(IntentRequest(text="hello", model_override=None, yolo=True))

    assert result.degraded is True
    assert result.used_model == "ollama-local"
    assert "could not reach any configured providers" in result.response_text


@pytest.mark.anyio
async def test_router_dispatches_system_control_requests(tmp_path) -> None:
    """Obvious system-control prompts should route into the dedicated module."""

    config = load_config(tmp_path / "missing.toml")
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text="provider answer",
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(text="show me the active window", model_override="codex-cli", yolo=False)
    )

    assert result.intent == "system_control"
    assert result.target_module == "system_control"
    assert result.used_model == "codex-cli"


@pytest.mark.anyio
async def test_router_dispatches_notes_requests(tmp_path) -> None:
    """Obvious notes prompts should route into the Phase 7 notes module."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"append_inbox","arguments":{"content":"Capture this"}}',
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(text="note: capture this", model_override="codex-cli", yolo=False)
    )

    assert result.intent == "notes"
    assert result.target_module == "notes"
    assert result.used_model == "codex-cli"


@pytest.mark.anyio
async def test_router_dispatches_macro_requests(tmp_path) -> None:
    """Explicit macro prompts should route into the Phase 15 module."""

    config = load_config(tmp_path / "missing.toml")
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text="provider answer",
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        macros_module=FakeMacrosModule(),
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(text="run the desk summary macro", model_override="codex-cli", yolo=False)
    )

    assert result.intent == "macros"
    assert result.target_module == "macros"
    assert result.used_model == "codex-cli"


@pytest.mark.anyio
async def test_router_dispatches_memory_requests(tmp_path) -> None:
    """Explicit persistent-memory prompts should route into the Phase 10 module."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"propose_global","arguments":{"content":"User likes concise answers."}}',
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(
            text="remember that I like concise answers",
            model_override="codex-cli",
            yolo=False,
        )
    )

    assert result.intent == "memory"
    assert result.target_module == "memory"
    assert result.used_model == "codex-cli"


@pytest.mark.anyio
async def test_router_dispatches_git_github_requests(tmp_path) -> None:
    """Explicit git/github prompts should route into the Phase 12 module."""

    config = load_config(tmp_path / "config.toml")
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"summarize_diff","arguments":{}}',
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(text="summarize the git diff", model_override="codex-cli", yolo=False)
    )

    assert result.intent == "git_github"
    assert result.target_module == "git_github"


@pytest.mark.anyio
async def test_router_dispatches_calendar_requests(tmp_path) -> None:
    """Explicit calendar prompts should route into the Phase 14 module."""

    config = load_config(tmp_path / "config.toml")
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text="provider answer",
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        calendar_module=FakeCalendarModule(),
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(text="show my calendar today", model_override="codex-cli", yolo=False)
    )

    assert result.intent == "calendar"
    assert result.target_module == "calendar"


@pytest.mark.anyio
async def test_router_dispatches_task_requests(tmp_path) -> None:
    """Explicit task prompts should route into the Phase 13 tasks module."""

    config = load_config(tmp_path / "config.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    (config.notes.projects_dir / "nyx").mkdir(parents=True)
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"add_task","arguments":{"project":"nyx","content":"Write tests"}}',
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(text="add a task for nyx to write tests", model_override="codex-cli", yolo=False)
    )

    assert result.intent == "tasks"
    assert result.target_module == "tasks"
    assert result.used_model == "codex-cli"


@pytest.mark.anyio
async def test_router_dispatches_rag_requests(tmp_path) -> None:
    """Explicit local-search prompts should route into the Phase 8 RAG module."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    config.rag.db_path = tmp_path / "rag"
    alpha_dir = config.notes.projects_dir / "alpha"
    alpha_dir.mkdir(parents=True)
    (alpha_dir / "notes.md").write_text("remember auth rotation policy\n", encoding="utf-8")

    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"search_project","arguments":{"project":"alpha","query":"auth rotation"}}',
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(
            text="search project alpha for auth rotation",
            model_override="codex-cli",
            yolo=False,
        )
    )

    assert result.intent == "rag"
    assert result.target_module == "rag"
    assert result.used_model == "codex-cli"


@pytest.mark.anyio
async def test_router_dispatches_screen_context_requests(tmp_path) -> None:
    """Explicit screen-analysis prompts should route into the Phase 11 module."""

    config = load_config(tmp_path / "config.toml")
    registry = FakeRegistry(
        result=ProviderQueryResult(
            provider_name="openai",
            provider_type="openai",
            model_name="gpt-4o",
            text="vision answer",
            fallback_used=False,
        )
    )
    router = IntentRouter(
        config=config,
        bridge=StubBridge("Linux"),
        provider_registry=registry,
        logger=logging.getLogger("test"),
    )

    result = await router.route(
        IntentRequest(
            text="what is on my screen?",
            model_override="openai",
            yolo=False,
        )
    )

    assert result.intent == "screen_context"
    assert result.target_module == "screen_context"
