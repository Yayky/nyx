"""Live web lookup services for Nyx.

Phase 19 adds a local service wrapper around SearXNG and Brave Search so the
rest of Nyx can perform explicit fresh-information lookups without embedding
provider-specific HTTP logic inside the intent router.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import logging
import re
from typing import Any

import httpx

from nyx.config import NyxConfig

_WHITESPACE_PATTERN = re.compile(r"\s+")


class WebLookupError(RuntimeError):
    """Raised when Nyx cannot perform or summarize a web lookup."""


@dataclass(slots=True)
class SearchHit:
    """One normalized web-search result."""

    title: str
    url: str
    snippet: str
    source: str
    engine: str | None = None


@dataclass(slots=True)
class FetchedPage:
    """Normalized page content returned by the fetch step."""

    url: str
    title: str | None
    content: str
    content_type: str


class _HTMLTextExtractor(HTMLParser):
    """Small HTML-to-text extractor for URL summarization."""

    def __init__(self) -> None:
        """Initialize parser state for title and visible text collection."""

        super().__init__(convert_charrefs=True)
        self._text_chunks: list[str] = []
        self._title_chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    @property
    def title(self) -> str | None:
        """Return the extracted `<title>` text when present."""

        title = _normalize_text(" ".join(self._title_chunks))
        return title or None

    @property
    def text(self) -> str:
        """Return normalized visible page text."""

        return _normalize_text(" ".join(self._text_chunks))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track tags that should be skipped or treated specially."""

        del attrs
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if lowered == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        """Close skip or title sections."""

        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        """Collect title or visible text nodes."""

        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_chunks.append(data)
            return
        self._text_chunks.append(data)


class WebLookupService:
    """Perform SearXNG searches, Brave fallback, and URL fetches."""

    def __init__(
        self,
        config: NyxConfig,
        logger: logging.Logger | None = None,
        client_factory: type[httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        """Store config and HTTP-client dependencies for web lookup operations."""

        self.config = config
        self.logger = logger or logging.getLogger("nyx.web")
        self._client_factory = client_factory

    async def search(self, query: str, limit: int = 5) -> tuple[list[SearchHit], str]:
        """Search the web using SearXNG first and Brave as fallback."""

        normalized_limit = max(1, min(limit, 10))

        try:
            searx_hits = await self._search_searxng(query=query, limit=normalized_limit)
        except (httpx.HTTPError, WebLookupError) as exc:
            self.logger.warning("SearXNG lookup failed: %s", exc)
            searx_hits = []

        if searx_hits:
            return searx_hits, "searxng"

        brave_key = self.config.web.brave_api_key.strip()
        if not brave_key:
            raise WebLookupError(
                "SearXNG returned no results and Brave fallback is not configured."
            )

        try:
            brave_hits = await self._search_brave(query=query, limit=normalized_limit)
        except httpx.HTTPError as exc:
            raise WebLookupError(f"Brave fallback failed: {exc}") from exc

        if not brave_hits:
            raise WebLookupError("No web search results found from SearXNG or Brave.")
        return brave_hits, "brave"

    async def fetch_url(self, url: str) -> FetchedPage:
        """Fetch one URL and return summarized page text for provider prompting."""

        timeout = httpx.Timeout(10.0, connect=5.0)
        headers = {"User-Agent": "Nyx/0.1 (+local-first assistant)"}
        async with self._client_factory(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type in {"text/plain", "application/json"}:
            text = _normalize_text(response.text)
            if not text:
                raise WebLookupError(f"URL returned empty text content: {url}")
            return FetchedPage(
                url=str(response.url),
                title=None,
                content=text[:12000],
                content_type=content_type or "text/plain",
            )

        extractor = _HTMLTextExtractor()
        extractor.feed(response.text)
        extractor.close()
        if not extractor.text:
            raise WebLookupError(f"URL returned no readable text content: {url}")
        return FetchedPage(
            url=str(response.url),
            title=extractor.title,
            content=extractor.text[:12000],
            content_type=content_type or "text/html",
        )

    async def _search_searxng(self, query: str, limit: int) -> list[SearchHit]:
        """Query the configured SearXNG instance and normalize results."""

        base_url = self.config.web.searxng_url.rstrip("/")
        timeout = httpx.Timeout(self.config.web.fallback_timeout_seconds, connect=2.0)
        async with self._client_factory(timeout=timeout) as client:
            response = await client.get(
                f"{base_url}/search",
                params={"q": query, "format": "json"},
            )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise WebLookupError("SearXNG returned an invalid JSON result shape.")

        hits: list[SearchHit] = []
        for entry in results[:limit]:
            if not isinstance(entry, dict):
                continue
            url = _normalize_text(str(entry.get("url", "")))
            title = _normalize_text(str(entry.get("title", "")))
            if not url or not title:
                continue
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=_normalize_text(str(entry.get("content", "")))[:300],
                    source="searxng",
                    engine=_normalize_text(str(entry.get("engine", ""))) or None,
                )
            )
        return hits

    async def _search_brave(self, query: str, limit: int) -> list[SearchHit]:
        """Query Brave Search and normalize results for Nyx."""

        timeout = httpx.Timeout(self.config.web.fallback_timeout_seconds, connect=2.0)
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.config.web.brave_api_key,
        }
        async with self._client_factory(timeout=timeout, headers=headers) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
            )
        response.raise_for_status()
        payload = response.json()
        web_section = payload.get("web", {})
        results = web_section.get("results", []) if isinstance(web_section, dict) else []
        if not isinstance(results, list):
            raise WebLookupError("Brave Search returned an invalid JSON result shape.")

        hits: list[SearchHit] = []
        for entry in results[:limit]:
            if not isinstance(entry, dict):
                continue
            url = _normalize_text(str(entry.get("url", "")))
            title = _normalize_text(str(entry.get("title", "")))
            if not url or not title:
                continue
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=_normalize_text(str(entry.get("description", "")))[:300],
                    source="brave",
                    engine="brave",
                )
            )
        return hits


def _normalize_text(raw_text: str) -> str:
    """Collapse repeated whitespace into a readable single-spaced string."""

    return _WHITESPACE_PATTERN.sub(" ", raw_text).strip()
