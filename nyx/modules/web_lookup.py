"""Web and live-info lookup module for Nyx."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from nyx.config import NyxConfig
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry
from nyx.web import FetchedPage, SearchHit, WebLookupService

_WEB_PATTERNS = (
    re.compile(r"\bsearch\b.+\b(web|online|internet)\b", re.IGNORECASE),
    re.compile(r"\blook\s+up\b", re.IGNORECASE),
    re.compile(r"\blookup\b", re.IGNORECASE),
    re.compile(r"\blatest\b.+\b(on|about|for)\b", re.IGNORECASE),
    re.compile(r"\btoday'?s\b.+\b(news|headlines)\b", re.IGNORECASE),
    re.compile(r"https?://\S+", re.IGNORECASE),
)
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {"search_web", "summarize_url", "reject"}


@dataclass(slots=True)
class WebLookupPlan:
    """Validated provider-produced action plan for a web request."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class WebLookupResult:
    """Structured result returned by the Phase 19 web module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class WebLookupModule:
    """Handle explicit web searches and URL summaries."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        web_service: WebLookupService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit config, providers, and HTTP service."""

        self.config = config
        self.provider_registry = provider_registry
        self.web_service = web_service or WebLookupService(config=config)
        self.logger = logger or logging.getLogger("nyx.modules.web_lookup")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious explicit web-lookup request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _WEB_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> WebLookupResult:
        """Handle one explicit web search or URL summary request."""

        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text),
            context=self._planner_context(),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Web planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        if plan.operation == "summarize_url":
            url = self._require_string_argument(plan.arguments, "url")
            focus = self._optional_string_argument(plan.arguments, "focus")
            page = await self.web_service.fetch_url(url)
            summary_result = await self.provider_registry.query(
                prompt=self._build_url_summary_prompt(request_text, page, focus),
                context=self._url_summary_context(page, focus),
                preferred_provider_name=model_override,
            )
            return self._result_from_provider(
                summary_result,
                summary_result.text.strip(),
                plan.operation,
                degraded=provider_result.fallback_used or summary_result.fallback_used,
            )

        query = self._require_string_argument(plan.arguments, "query")
        limit = self._require_int_argument(plan.arguments, "limit", default=5)
        hits, backend = await self.web_service.search(query=query, limit=limit)
        summary_result = await self.provider_registry.query(
            prompt=self._build_search_summary_prompt(request_text, query, hits, backend),
            context=self._search_summary_context(query, hits, backend),
            preferred_provider_name=model_override,
        )
        return self._result_from_provider(
            summary_result,
            summary_result.text.strip(),
            plan.operation,
            degraded=backend != "searxng" or provider_result.fallback_used or summary_result.fallback_used,
        )

    def _planner_context(self) -> dict[str, Any]:
        """Return static planning context for the web planner."""

        return {
            "module": "web_lookup",
            "searxng_url": self.config.web.searxng_url,
            "brave_fallback_enabled": bool(self.config.web.brave_api_key.strip()),
            "fallback_timeout_seconds": self.config.web.fallback_timeout_seconds,
        }

    def _build_planner_prompt(self, request_text: str) -> str:
        """Build the provider prompt that selects the web action to execute."""

        return (
            "You are Nyx's Phase 19 web planner. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: search_web, summarize_url, reject. "
            "Use summarize_url only when the user clearly supplied or referenced a URL. "
            "Use search_web for explicit live-info or web-search requests. "
            "If the request is not a web or URL lookup request, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- search_web: {"query": str, "limit": int}\n'
            '- summarize_url: {"url": str, "focus": str|null}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _build_search_summary_prompt(
        self,
        request_text: str,
        query: str,
        hits: list[SearchHit],
        backend: str,
    ) -> str:
        """Build the provider prompt that turns search hits into a user answer."""

        rendered_hits = json.dumps(
            [
                {
                    "title": hit.title,
                    "url": hit.url,
                    "snippet": hit.snippet,
                    "source": hit.source,
                    "engine": hit.engine,
                }
                for hit in hits
            ],
            indent=2,
        )
        return (
            "You are Nyx's Phase 19 search summarizer. "
            "Answer the user's request using only the provided fresh web search hits. "
            "Be concise, mention uncertainty when the hits conflict, and include 2-5 relevant source URLs inline. "
            f"The search backend used was {backend}. Search query: {query}\n\n"
            f"Original user request: {request_text}\n\n"
            f"Search hits:\n{rendered_hits}"
        )

    def _build_url_summary_prompt(
        self,
        request_text: str,
        page: FetchedPage,
        focus: str | None,
    ) -> str:
        """Build the provider prompt that summarizes one fetched URL."""

        focus_line = f"Focus: {focus}\n" if focus else ""
        title_line = f"Page title: {page.title}\n" if page.title else ""
        return (
            "You are Nyx's Phase 19 URL summarizer. "
            "Summarize the fetched page content concisely and mention the source URL. "
            "If the user asked a focused question, answer that question using only the fetched page text.\n\n"
            f"Original user request: {request_text}\n"
            f"{focus_line}{title_line}"
            f"Source URL: {page.url}\n"
            f"Content type: {page.content_type}\n\n"
            f"Fetched page text:\n{page.content}"
        )

    def _search_summary_context(
        self,
        query: str,
        hits: list[SearchHit],
        backend: str,
    ) -> dict[str, Any]:
        """Return structured context for search-hit summarization."""

        return {
            "module": "web_lookup",
            "operation": "search_web",
            "query": query,
            "backend": backend,
            "result_count": len(hits),
        }

    def _url_summary_context(self, page: FetchedPage, focus: str | None) -> dict[str, Any]:
        """Return structured context for one URL-summary request."""

        return {
            "module": "web_lookup",
            "operation": "summarize_url",
            "url": page.url,
            "title": page.title,
            "focus": focus,
        }

    def _parse_plan(self, planner_text: str) -> WebLookupPlan:
        """Parse and validate the JSON plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Web planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported web operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Web planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Web planner 'rationale' must be a string when present.")

        return WebLookupPlan(operation=operation, arguments=arguments, rationale=rationale)

    def _extract_json_object(self, text: str) -> str:
        """Extract one JSON object from raw provider output."""

        fenced_match = _JSON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        object_match = _JSON_OBJECT_PATTERN.search(text)
        if object_match is None:
            raise ValueError("Web planner did not return a JSON object.")
        return object_match.group(0).strip()

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from a planner payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Web action is missing string argument '{key}'.")
        return value.strip()

    def _optional_string_argument(self, arguments: dict[str, Any], key: str) -> str | None:
        """Return one optional string argument when present and non-empty."""

        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Web action argument '{key}' must be a string when present.")
        normalized = value.strip()
        return normalized or None

    def _require_int_argument(self, arguments: dict[str, Any], key: str, default: int) -> int:
        """Return one bounded integer argument from a planner payload."""

        value = arguments.get(key, default)
        if not isinstance(value, int):
            raise ValueError(f"Web action argument '{key}' must be an integer.")
        return max(1, min(value, 10))

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
        degraded: bool | None = None,
    ) -> WebLookupResult:
        """Build a web-module result while preserving provider metadata."""

        return WebLookupResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used if degraded is None else degraded,
            operation=operation,
        )
