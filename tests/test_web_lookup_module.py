"""Tests for the Phase 19 web lookup service and module."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from nyx.config import load_config
from nyx.modules.web_lookup import WebLookupModule
from nyx.providers.base import ProviderQueryResult
from nyx.web.service import FetchedPage, SearchHit, WebLookupService


class FakeResponse:
    """Minimal HTTP response object for web-service tests."""

    def __init__(
        self,
        *,
        json_payload: dict[str, Any] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        status_code: int = 200,
        url: str = "https://example.com",
    ) -> None:
        """Store response metadata for deterministic HTTP tests."""

        self._json_payload = json_payload
        self.text = text
        self.headers = headers or {}
        self.status_code = status_code
        self.url = url

    def raise_for_status(self) -> None:
        """Raise on non-success status codes."""

        if self.status_code >= 400:
            raise RuntimeError(f"http status {self.status_code}")

    def json(self) -> dict[str, Any]:
        """Return the configured JSON payload."""

        assert self._json_payload is not None
        return self._json_payload


class FakeAsyncClient:
    """Minimal async HTTP client used to isolate the web service."""

    responses: list[FakeResponse] = []

    def __init__(self, **kwargs: Any) -> None:
        """Ignore client options while preserving the context-manager contract."""

        del kwargs

    async def __aenter__(self) -> FakeAsyncClient:
        """Return the fake client."""

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """No-op async context-manager exit."""

        del exc_type, exc, tb

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Return the next configured fake response."""

        del url, kwargs
        if not self.responses:
            raise AssertionError("No fake responses remaining.")
        return self.responses.pop(0)


@dataclass
class SequentialRegistry:
    """Provider registry stub that returns queued provider results."""

    results: list[ProviderQueryResult]
    prompts: list[str] = field(default_factory=list)

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
        preferred_tiers: tuple[str, ...] | None = None,
    ) -> ProviderQueryResult:
        """Return queued provider results in call order."""

        del context, preferred_provider_name, preferred_tiers
        self.prompts.append(prompt)
        if not self.results:
            raise AssertionError("Unexpected provider query.")
        return self.results.pop(0)


@pytest.mark.anyio
async def test_web_service_search_parses_searxng_results(tmp_path) -> None:
    """SearXNG JSON results should normalize into search hits."""

    FakeAsyncClient.responses = [
        FakeResponse(
            json_payload={
                "results": [
                    {
                        "title": "Nyx docs",
                        "url": "https://example.com/nyx",
                        "content": "Local-first assistant docs",
                        "engine": "duckduckgo",
                    }
                ]
            }
        )
    ]
    service = WebLookupService(
        config=load_config(tmp_path / "missing.toml"),
        client_factory=FakeAsyncClient,
    )

    hits, backend = await service.search("nyx docs")

    assert backend == "searxng"
    assert hits == [
        SearchHit(
            title="Nyx docs",
            url="https://example.com/nyx",
            snippet="Local-first assistant docs",
            source="searxng",
            engine="duckduckgo",
        )
    ]


@pytest.mark.anyio
async def test_web_service_falls_back_to_brave_when_searxng_is_empty(tmp_path) -> None:
    """Empty SearXNG results should trigger Brave fallback when configured."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[web]
brave_api_key = "brave-token"
""".strip()
    )
    service = WebLookupService(config=load_config(config_path))

    async def fake_search_searxng(query: str, limit: int) -> list[SearchHit]:
        del query, limit
        return []

    async def fake_search_brave(query: str, limit: int) -> list[SearchHit]:
        del query, limit
        return [
            SearchHit(
                title="Fallback result",
                url="https://example.com/fallback",
                snippet="Brave fallback hit",
                source="brave",
                engine="brave",
            )
        ]

    service._search_searxng = fake_search_searxng  # type: ignore[method-assign]
    service._search_brave = fake_search_brave  # type: ignore[method-assign]

    hits, backend = await service.search("latest nyx news")

    assert backend == "brave"
    assert hits[0].title == "Fallback result"


@pytest.mark.anyio
async def test_web_service_fetch_url_extracts_html_text(tmp_path) -> None:
    """Fetched HTML pages should return a title plus normalized visible text."""

    FakeAsyncClient.responses = [
        FakeResponse(
            text="""
<html>
  <head><title>Nyx Article</title><style>.x{}</style></head>
  <body><h1>Nyx ships web lookup</h1><p>Fresh search support is live.</p></body>
</html>
""".strip(),
            headers={"content-type": "text/html; charset=utf-8"},
            url="https://example.com/article",
        )
    ]
    service = WebLookupService(
        config=load_config(tmp_path / "missing.toml"),
        client_factory=FakeAsyncClient,
    )

    page = await service.fetch_url("https://example.com/article")

    assert page.title == "Nyx Article"
    assert "Nyx ships web lookup" in page.content
    assert "Fresh search support is live." in page.content


@pytest.mark.anyio
async def test_web_module_searches_and_summarizes_hits(tmp_path) -> None:
    """Search requests should run the planner, web lookup, and provider summary flow."""

    registry = SequentialRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"search_web","arguments":{"query":"nyx web lookup","limit":3},"rationale":"live search"}',
                fallback_used=False,
            ),
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text="Nyx now supports live web lookup. Sources: https://example.com/nyx https://example.com/docs",
                fallback_used=False,
            ),
        ]
    )
    module = WebLookupModule(
        config=load_config(tmp_path / "missing.toml"),
        provider_registry=registry,
        web_service=SimpleNamespace(
            search=lambda query, limit: _async_return(
                (
                    [
                        SearchHit(
                            title="Nyx web lookup",
                            url="https://example.com/nyx",
                            snippet="Nyx ships live web search.",
                            source="searxng",
                            engine="duckduckgo",
                        ),
                        SearchHit(
                            title="Docs",
                            url="https://example.com/docs",
                            snippet="Phase 19 docs.",
                            source="searxng",
                            engine="duckduckgo",
                        ),
                    ],
                    "searxng",
                )
            )
        ),
    )

    result = await module.handle("search web for nyx web lookup")

    assert "Nyx now supports live web lookup" in result.response_text
    assert result.operation == "search_web"
    assert len(registry.prompts) == 2


@pytest.mark.anyio
async def test_web_module_fetches_and_summarizes_urls(tmp_path) -> None:
    """URL requests should fetch the page and summarize it through the provider layer."""

    registry = SequentialRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"summarize_url","arguments":{"url":"https://example.com/article","focus":"main points"},"rationale":"url summary"}',
                fallback_used=False,
            ),
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text="The article says Nyx now supports explicit web lookup through SearXNG with Brave fallback.",
                fallback_used=False,
            ),
        ]
    )
    module = WebLookupModule(
        config=load_config(tmp_path / "missing.toml"),
        provider_registry=registry,
        web_service=SimpleNamespace(
            fetch_url=lambda url: _async_return(
                FetchedPage(
                    url=url,
                    title="Nyx Article",
                    content="Nyx now supports explicit web lookup with SearXNG and Brave fallback.",
                    content_type="text/html",
                )
            )
        ),
    )

    result = await module.handle("summarize https://example.com/article")

    assert "explicit web lookup" in result.response_text
    assert result.operation == "summarize_url"
    assert len(registry.prompts) == 2


async def _async_return(value):
    """Return a value through an awaitable helper for test doubles."""

    return value
