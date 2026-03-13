"""Google Calendar service and local `.ical` fallback for Nyx."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import Any
import uuid

import httpx

from nyx.calendar.ical import CachedCalendarEvent, IcalCache
from nyx.config import NyxConfig

_GOOGLE_CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar"]


class CalendarUnavailableError(RuntimeError):
    """Raised when Nyx cannot reach calendar data or create events."""


@dataclass(slots=True)
class CalendarEvent:
    """Normalized calendar event returned by the Phase 14 service."""

    event_id: str
    summary: str
    start: str
    end: str
    location: str | None
    description: str | None
    source: str


class CalendarService:
    """Access Google Calendar with a local `.ical` cache for read fallback."""

    def __init__(
        self,
        config: NyxConfig,
        cache: IcalCache | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the service with config, cache, and logger dependencies."""

        self.config = config
        self.logger = logger or logging.getLogger("nyx.calendar.service")
        self.cache = cache or IcalCache(self._cache_path())

    async def list_events(
        self,
        *,
        start_iso: str,
        end_iso: str,
        limit: int = 10,
    ) -> tuple[list[CalendarEvent], str]:
        """List events from Google Calendar or fall back to the cached `.ical` file."""

        try:
            events = await asyncio.to_thread(
                self._list_events_google,
                start_iso,
                end_iso,
                limit,
            )
        except Exception as exc:
            cached = await self._read_cached_events(start_iso=start_iso, end_iso=end_iso)
            if cached:
                self.logger.warning("Google Calendar unavailable, using .ical cache: %s", exc)
                return cached, "ical-cache"
            raise CalendarUnavailableError(
                f"Nyx could not reach Google Calendar and no offline .ical cache was available: {exc}"
            ) from exc

        await self.cache.write_events(
            CachedCalendarEvent(
                event_id=event.event_id,
                summary=event.summary,
                start=event.start,
                end=event.end,
                location=event.location,
                description=event.description,
            )
            for event in events
        )
        return events, "google"

    async def create_event(
        self,
        *,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: str | None = None,
        location: str | None = None,
    ) -> CalendarEvent:
        """Create one Google Calendar event."""

        try:
            return await asyncio.to_thread(
                self._create_event_google,
                summary,
                start_iso,
                end_iso,
                description,
                location,
            )
        except Exception as exc:
            raise CalendarUnavailableError(
                f"Nyx could not create the calendar event: {exc}"
            ) from exc

    async def _read_cached_events(
        self,
        *,
        start_iso: str,
        end_iso: str,
    ) -> list[CalendarEvent]:
        """Read cached `.ical` events and filter them to the requested time window."""

        events = await self.cache.read_events()
        start = _parse_iso(start_iso)
        end = _parse_iso(end_iso)
        filtered: list[CalendarEvent] = []
        for event in events:
            event_start = _parse_iso(event.start)
            event_end = _parse_iso(event.end)
            if event_end < start or event_start > end:
                continue
            filtered.append(
                CalendarEvent(
                    event_id=event.event_id,
                    summary=event.summary,
                    start=event.start,
                    end=event.end,
                    location=event.location,
                    description=event.description,
                    source="ical-cache",
                )
            )
        return filtered

    def _list_events_google(self, start_iso: str, end_iso: str, limit: int) -> list[CalendarEvent]:
        """Perform the blocking Google Calendar API list call synchronously."""

        service = self._google_service()
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=_google_rfc3339(start_iso),
                timeMax=_google_rfc3339(end_iso),
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        items = response.get("items", [])
        return [self._from_google_item(item) for item in items if isinstance(item, dict)]

    def _create_event_google(
        self,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: str | None,
        location: str | None,
    ) -> CalendarEvent:
        """Perform the blocking Google Calendar API create call synchronously."""

        service = self._google_service()
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": _google_rfc3339(start_iso)},
            "end": {"dateTime": _google_rfc3339(end_iso)},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        created = service.events().insert(calendarId="primary", body=body).execute()
        if not isinstance(created, dict):
            raise CalendarUnavailableError("Google Calendar returned an unexpected create-event payload.")
        return self._from_google_item(created)

    def _google_service(self):
        """Return an authenticated Google Calendar service client."""

        if self.config.calendar.provider != "google":
            raise CalendarUnavailableError(
                f"Unsupported calendar provider '{self.config.calendar.provider}'."
            )

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise CalendarUnavailableError(
                "Google Calendar dependencies are missing. Install google-api-python-client, "
                "google-auth-httplib2, and google-auth-oauthlib."
            ) from exc

        credentials_path = self.config.calendar.credentials_path
        if not credentials_path.exists():
            raise CalendarUnavailableError(
                f"Google Calendar credentials file not found at {credentials_path}."
            )

        token_path = self._token_path()
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _GOOGLE_CALENDAR_SCOPE)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path),
                _GOOGLE_CALENDAR_SCOPE,
            )
            creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("calendar", "v3", credentials=creds)

    def _from_google_item(self, item: dict[str, Any]) -> CalendarEvent:
        """Normalize one Google Calendar API event resource."""

        event_id = item.get("id") or uuid.uuid4().hex
        summary = item.get("summary") or "(untitled event)"
        start_info = item.get("start", {})
        end_info = item.get("end", {})
        start = _normalize_google_time(start_info)
        end = _normalize_google_time(end_info)
        return CalendarEvent(
            event_id=str(event_id),
            summary=str(summary),
            start=start,
            end=end,
            location=item.get("location"),
            description=item.get("description"),
            source="google",
        )

    def _token_path(self) -> Path:
        """Return the local OAuth token cache path."""

        return self.config.config_path.parent / "google_token.json"

    def _cache_path(self) -> Path:
        """Return the local `.ical` cache path used for fallback reads."""

        return self.config.config_path.parent / "calendar_cache.ics"


def _normalize_google_time(payload: Any) -> str:
    """Normalize Google `date` / `dateTime` fields into ISO datetime strings."""

    if not isinstance(payload, dict):
        raise CalendarUnavailableError("Google Calendar event payload is missing its time fields.")
    date_time = payload.get("dateTime")
    if isinstance(date_time, str) and date_time:
        return _parse_iso(date_time).isoformat()
    date_only = payload.get("date")
    if isinstance(date_only, str) and date_only:
        return datetime.fromisoformat(f"{date_only}T00:00:00+00:00").isoformat()
    raise CalendarUnavailableError("Google Calendar event payload did not include a date or dateTime.")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO datetime string into an aware datetime."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _google_rfc3339(value: str) -> str:
    """Convert ISO datetimes into RFC3339 strings accepted by Google."""

    return _parse_iso(value).astimezone(UTC).isoformat()
