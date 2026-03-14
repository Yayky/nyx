"""Calendar module for Nyx.

Phase 14 adds explicit calendar queries and event creation using Google
Calendar as the primary backend with a local `.ical` cache for read-only
fallback when the API is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import re
from typing import Any

from nyx.calendar.service import CalendarEvent, CalendarService
from nyx.config import NyxConfig
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_CALENDAR_PATTERNS = (
    re.compile(r"\bcalendar\b", re.IGNORECASE),
    re.compile(r"\bagenda\b", re.IGNORECASE),
    re.compile(r"\bschedule\b", re.IGNORECASE),
    re.compile(r"\bmeeting\b", re.IGNORECASE),
    re.compile(r"\bevent\b", re.IGNORECASE),
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {"list_events", "create_event", "reject"}


@dataclass(slots=True)
class CalendarPlan:
    """Validated provider-produced calendar action plan."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class CalendarResult:
    """Structured result returned by the Phase 14 calendar module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class CalendarModule:
    """Handle explicit calendar queries and event creation requests."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        calendar_service: CalendarService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit provider and calendar dependencies."""

        self.config = config
        self.provider_registry = provider_registry
        self.calendar_service = calendar_service or CalendarService(config=config)
        self.logger = logger or logging.getLogger("nyx.modules.calendar")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious calendar request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _CALENDAR_PATTERNS)

    async def handle(self, request_text: str, model_override: str | None = None) -> CalendarResult:
        """Handle one explicit calendar request."""

        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text),
            context=self._planner_context(),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Calendar planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        if plan.operation == "list_events":
            start = self._require_string_argument(plan.arguments, "start")
            end = self._require_string_argument(plan.arguments, "end")
            limit = int(plan.arguments.get("limit", 10))
            events, source = await self.calendar_service.list_events(
                start_iso=start,
                end_iso=end,
                limit=limit,
            )
            if not events:
                return self._result_from_provider(
                    provider_result,
                    f"No calendar events found between {start} and {end}.",
                    plan.operation,
                    degraded=source != "google",
                )
            return self._result_from_provider(
                provider_result,
                self._format_events(events, source),
                plan.operation,
                degraded=source != "google",
            )

        summary = self._require_string_argument(plan.arguments, "summary")
        start = self._require_string_argument(plan.arguments, "start")
        end = self._require_string_argument(plan.arguments, "end")
        description = self._optional_string_argument(plan.arguments, "description")
        location = self._optional_string_argument(plan.arguments, "location")
        calendar_id = self._optional_string_argument(plan.arguments, "calendar_id")
        event = await self.calendar_service.create_event(
            summary=summary,
            start_iso=start,
            end_iso=end,
            description=description,
            location=location,
            calendar_id=calendar_id,
        )
        response_text = (
            f"Created calendar event '{event.summary}' from {event.start} to {event.end}."
        )
        if event.location:
            response_text += f" Location: {event.location}."
        if event.calendar_name:
            response_text += f" Calendar: {event.calendar_name}."
        elif event.calendar_id:
            response_text += f" Calendar ID: {event.calendar_id}."
        return self._result_from_provider(provider_result, response_text, plan.operation)

    def _planner_context(self) -> dict[str, Any]:
        """Return static planning context for calendar requests."""

        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        return {
            "module": "calendar",
            "provider": self.config.calendar.provider,
            "auth_mode": self.config.calendar.auth_mode,
            "default_calendar_id": self.config.calendar.default_calendar_id,
            "calendar_ids": list(self.config.calendar.calendar_ids),
            "include_all_calendars": self.config.calendar.include_all_calendars,
            "current_time": now.isoformat(),
            "today_start": today_start.isoformat(),
            "today_end": tomorrow_start.isoformat(),
            "supports_offline_read_fallback": True,
            "time_format": "Use ISO 8601 with timezone offsets in all returned timestamps.",
        }

    def _build_planner_prompt(self, request_text: str) -> str:
        """Build the provider prompt for one calendar request."""

        return (
            "You are Nyx's Phase 14 calendar planner. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown. Allowed operations: list_events, create_event, reject. "
            "Use list_events for agenda/calendar lookup requests and create_event for scheduling requests. "
            "All timestamps must be ISO 8601 strings with timezone offsets. "
            "If the request names a specific calendar, include calendar_id in the create_event arguments. "
            "Agenda requests may span multiple visible calendars when Nyx is configured to do so. "
            "If the request is not a calendar action, return "
            '{"operation":"reject","arguments":{"reason":"..."},"rationale":"..."}.\n\n'
            "Argument rules:\n"
            '- list_events: {"start": str, "end": str, "limit": int}\n'
            '- create_event: {"summary": str, "start": str, "end": str, "description": str|null, "location": str|null, "calendar_id": str|null}\n'
            '- reject: {"reason": str}\n\n'
            f"User request: {request_text}"
        )

    def _parse_plan(self, planner_text: str) -> CalendarPlan:
        """Parse and validate the JSON calendar plan returned by the provider."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Calendar planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported calendar operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Calendar planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Calendar planner 'rationale' must be a string when present.")

        return CalendarPlan(operation=operation, arguments=arguments, rationale=rationale)

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
            raise ValueError("Calendar planner did not return a JSON object.")
        return object_match.group(0).strip()

    def _format_events(self, events: list[CalendarEvent], source: str) -> str:
        """Render a concise agenda view from normalized calendar events."""

        header = "Calendar events"
        if source != "google":
            header += " (offline .ical cache)"
        header += ":"
        lines = [header]
        show_calendar_name = len({event.calendar_id for event in events}) > 1
        for event in events:
            line = f"- {event.start} -> {event.end}: {event.summary}"
            if show_calendar_name:
                calendar_label = event.calendar_name or event.calendar_id
                line += f" [{calendar_label}]"
            if event.location:
                line += f" @ {event.location}"
            lines.append(line)
        return "\n".join(lines)

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from a planner payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Calendar action is missing string argument '{key}'.")
        return value.strip()

    def _optional_string_argument(self, arguments: dict[str, Any], key: str) -> str | None:
        """Return one optional string argument when present and non-empty."""

        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Calendar action argument '{key}' must be a string when present.")
        normalized = value.strip()
        return normalized or None

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
        *,
        degraded: bool | None = None,
    ) -> CalendarResult:
        """Build a calendar result while preserving provider metadata."""

        return CalendarResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.degraded if degraded is None else degraded,
            operation=operation,
        )
