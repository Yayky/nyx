"""Tests for the Phase 14 calendar module and `.ical` fallback."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.calendar.ical import CachedCalendarEvent, IcalCache
from nyx.calendar.service import CalendarEvent, CalendarService
from nyx.config import load_config
from nyx.modules.calendar import CalendarModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class FakeProviderRegistry:
    """Minimal registry stub for provider-planned calendar requests."""

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


class FakeCalendarService:
    """Calendar service stub for calendar-module tests."""

    def __init__(
        self,
        *,
        list_response: tuple[list[CalendarEvent], str] | None = None,
        create_response: CalendarEvent | None = None,
    ) -> None:
        """Initialize the fake service with deterministic responses."""

        self.list_response = list_response
        self.create_response = create_response
        self.seen_list_args: dict[str, Any] | None = None
        self.seen_create_args: dict[str, Any] | None = None

    async def list_events(self, *, start_iso: str, end_iso: str, limit: int = 10) -> tuple[list[CalendarEvent], str]:
        """Return the configured list response."""

        self.seen_list_args = {"start_iso": start_iso, "end_iso": end_iso, "limit": limit}
        assert self.list_response is not None
        return self.list_response

    async def create_event(
        self,
        *,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: str | None = None,
        location: str | None = None,
    ) -> CalendarEvent:
        """Return the configured create response."""

        self.seen_create_args = {
            "summary": summary,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "description": description,
            "location": location,
        }
        assert self.create_response is not None
        return self.create_response


class GoogleSuccessCalendarService(CalendarService):
    """Calendar service variant that bypasses Google auth in unit tests."""

    def __init__(self, config, events: list[CalendarEvent], cache: IcalCache) -> None:
        """Initialize with deterministic Google list results."""

        super().__init__(config=config, cache=cache, logger=logging.getLogger("test"))
        self.events = events

    def _list_events_google(self, start_iso: str, end_iso: str, limit: int) -> list[CalendarEvent]:
        """Return deterministic Google events without real API calls."""

        del start_iso, end_iso, limit
        return list(self.events)


class GoogleFailureCalendarService(CalendarService):
    """Calendar service variant that forces `.ical` fallback in unit tests."""

    def __init__(self, config, cache: IcalCache) -> None:
        """Initialize with a deterministic fallback cache."""

        super().__init__(config=config, cache=cache, logger=logging.getLogger("test"))

    def _list_events_google(self, start_iso: str, end_iso: str, limit: int) -> list[CalendarEvent]:
        """Simulate a Google Calendar failure."""

        del start_iso, end_iso, limit
        raise RuntimeError("google down")


@pytest.mark.anyio
async def test_ical_cache_round_trips_events(tmp_path: Path) -> None:
    """The `.ical` cache should write and read simple event payloads."""

    cache = IcalCache(tmp_path / "calendar_cache.ics")
    await cache.write_events(
        [
            CachedCalendarEvent(
                event_id="evt-1",
                summary="Review Nyx roadmap",
                start="2026-03-14T10:00:00+00:00",
                end="2026-03-14T10:30:00+00:00",
                location="Home office",
                description="Bring notes",
            )
        ]
    )

    events = await cache.read_events()

    assert len(events) == 1
    assert events[0].summary == "Review Nyx roadmap"
    assert events[0].location == "Home office"


@pytest.mark.anyio
async def test_calendar_service_uses_ical_cache_when_google_unavailable(tmp_path: Path) -> None:
    """List requests should fall back to the `.ical` cache when Google fails."""

    config = load_config(tmp_path / "config.toml")
    cache = IcalCache(tmp_path / "calendar_cache.ics")
    await cache.write_events(
        [
            CachedCalendarEvent(
                event_id="evt-1",
                summary="Offline review",
                start="2026-03-14T10:00:00+00:00",
                end="2026-03-14T11:00:00+00:00",
                location=None,
                description=None,
            )
        ]
    )
    service = GoogleFailureCalendarService(config=config, cache=cache)

    events, source = await service.list_events(
        start_iso="2026-03-14T00:00:00+00:00",
        end_iso="2026-03-15T00:00:00+00:00",
    )

    assert source == "ical-cache"
    assert len(events) == 1
    assert events[0].summary == "Offline review"


@pytest.mark.anyio
async def test_calendar_module_lists_events_and_marks_fallback_degraded(tmp_path: Path) -> None:
    """Agenda requests should render events and mark cache fallback as degraded."""

    config = load_config(tmp_path / "config.toml")
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"list_events","arguments":{"start":"2026-03-14T00:00:00+00:00","end":"2026-03-15T00:00:00+00:00","limit":5}}',
            fallback_used=False,
        )
    )
    service = FakeCalendarService(
        list_response=(
            [
                CalendarEvent(
                    event_id="evt-1",
                    summary="Review Nyx roadmap",
                    start="2026-03-14T10:00:00+00:00",
                    end="2026-03-14T10:30:00+00:00",
                    location="Desk",
                    description=None,
                    source="ical-cache",
                )
            ],
            "ical-cache",
        )
    )
    module = CalendarModule(
        config=config,
        provider_registry=registry,
        calendar_service=service,  # type: ignore[arg-type]
        logger=logging.getLogger("test"),
    )

    result = await module.handle("what is on my calendar tomorrow?", model_override="codex-cli")

    assert result.degraded is True
    assert "offline .ical cache" in result.response_text
    assert "Review Nyx roadmap" in result.response_text


@pytest.mark.anyio
async def test_calendar_module_creates_event(tmp_path: Path) -> None:
    """Schedule requests should call the create-event service path."""

    config = load_config(tmp_path / "config.toml")
    registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"create_event","arguments":{"summary":"Nyx planning","start":"2026-03-14T10:00:00+00:00","end":"2026-03-14T10:30:00+00:00","description":"Discuss Phase 14","location":"Home office"}}',
            fallback_used=False,
        )
    )
    service = FakeCalendarService(
        create_response=CalendarEvent(
            event_id="evt-2",
            summary="Nyx planning",
            start="2026-03-14T10:00:00+00:00",
            end="2026-03-14T10:30:00+00:00",
            location="Home office",
            description="Discuss Phase 14",
            source="google",
        )
    )
    module = CalendarModule(
        config=config,
        provider_registry=registry,
        calendar_service=service,  # type: ignore[arg-type]
        logger=logging.getLogger("test"),
    )

    result = await module.handle(
        "schedule a nyx planning meeting tomorrow at 10am for 30 minutes",
        model_override="codex-cli",
    )

    assert "Created calendar event 'Nyx planning'" in result.response_text
    assert service.seen_create_args is not None
    assert service.seen_create_args["location"] == "Home office"


def test_calendar_module_matcher_is_conservative() -> None:
    """Only explicit calendar-like prompts should route into the Phase 14 module."""

    assert CalendarModule.matches_request("what is on my calendar tomorrow?") is True
    assert CalendarModule.matches_request("show my agenda for today") is True
    assert CalendarModule.matches_request("schedule a meeting tomorrow at 10am") is True
    assert CalendarModule.matches_request("write release notes for nyx") is False
