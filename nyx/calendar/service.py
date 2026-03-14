"""Google Calendar service and local `.ical` fallback for Nyx."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import Any
import uuid

from nyx.calendar.ical import CachedCalendarEvent, IcalCache
from nyx.config import NyxConfig

_GOOGLE_CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar"]


class CalendarUnavailableError(RuntimeError):
    """Raised when Nyx cannot reach calendar data or create events."""


@dataclass(slots=True)
class CalendarEvent:
    """Normalized calendar event returned by the Phase 14 service."""

    event_id: str
    calendar_id: str
    calendar_name: str | None
    summary: str
    start: str
    end: str
    location: str | None
    description: str | None
    source: str


class CalendarService:
    """Access Google Calendar with ADC, desktop OAuth, and `.ical` fallback."""

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
                calendar_id=event.calendar_id,
                calendar_name=event.calendar_name,
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
        calendar_id: str | None = None,
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
                calendar_id,
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
                    calendar_id=event.calendar_id,
                    calendar_name=event.calendar_name,
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
        calendars = self._resolved_calendar_targets_google(service)
        events: list[CalendarEvent] = []
        for calendar_id, calendar_name in calendars:
            response = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=_google_rfc3339(start_iso),
                    timeMax=_google_rfc3339(end_iso),
                    maxResults=limit,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            items = response.get("items", [])
            events.extend(
                self._from_google_item(
                    item,
                    default_calendar_id=calendar_id,
                    default_calendar_name=calendar_name,
                )
                for item in items
                if isinstance(item, dict)
            )
        events.sort(key=lambda event: event.start)
        return events[:limit]

    def _create_event_google(
        self,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: str | None,
        location: str | None,
        calendar_id: str | None,
    ) -> CalendarEvent:
        """Perform the blocking Google Calendar API create call synchronously."""

        service = self._google_service()
        target_calendar_id = calendar_id or self._default_calendar_id()
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": _google_rfc3339(start_iso)},
            "end": {"dateTime": _google_rfc3339(end_iso)},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        created = service.events().insert(calendarId=target_calendar_id, body=body).execute()
        if not isinstance(created, dict):
            raise CalendarUnavailableError("Google Calendar returned an unexpected create-event payload.")
        return self._from_google_item(
            created,
            default_calendar_id=target_calendar_id,
            default_calendar_name=None if target_calendar_id == "primary" else target_calendar_id,
        )

    def _google_service(self):
        """Return an authenticated Google Calendar service client."""

        if self.config.calendar.provider != "google":
            raise CalendarUnavailableError(
                f"Unsupported calendar provider '{self.config.calendar.provider}'."
            )

        try:
            import google.auth
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise CalendarUnavailableError(
                "Google Calendar dependencies are missing. Install google-api-python-client, "
                "google-auth-httplib2, and google-auth-oauthlib."
            ) from exc

        auth_mode = self.config.calendar.auth_mode
        creds = None
        adc_error: Exception | None = None
        oauth_error: Exception | None = None

        if auth_mode in {"auto", "adc"}:
            try:
                creds, _project_id = google.auth.default(scopes=_GOOGLE_CALENDAR_SCOPE)
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                if creds and creds.valid:
                    return build("calendar", "v3", credentials=creds)
            except Exception as exc:
                adc_error = exc
                if auth_mode == "adc":
                    raise CalendarUnavailableError(
                        "Nyx could not load Application Default Credentials. "
                        "Run `gcloud auth application-default login --client-id-file ~/.config/nyx/google_credentials.json "
                        "--scopes https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/calendar` "
                        "or switch to desktop OAuth mode."
                    ) from exc

        if auth_mode in {"auto", "desktop-oauth"}:
            credentials_path = self.config.calendar.credentials_path
            if credentials_path.exists():
                token_path = self._token_path()
                try:
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
                except Exception as exc:
                    oauth_error = exc
                    if auth_mode == "desktop-oauth":
                        raise
            elif auth_mode == "desktop-oauth":
                raise CalendarUnavailableError(
                    f"Google Calendar credentials file not found at {credentials_path}."
                )

        raise CalendarUnavailableError(
            "Nyx could not initialize Google Calendar credentials. "
            "Supported auth paths are: "
            "(1) Application Default Credentials via "
            "`gcloud auth application-default login --client-id-file ~/.config/nyx/google_credentials.json "
            "--scopes https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/calendar`, "
            f"(2) Nyx desktop OAuth credentials at {self.config.calendar.credentials_path}. "
            f"ADC error: {adc_error}. OAuth error: {oauth_error}."
        )

    def _resolved_calendar_targets_google(self, service) -> list[tuple[str, str | None]]:
        """Resolve the calendars queried for agenda/list requests."""

        if self.config.calendar.include_all_calendars:
            return self._list_accessible_calendars_google(service)
        if self.config.calendar.calendar_ids:
            accessible = dict(self._list_accessible_calendars_google(service))
            return [
                (calendar_id, accessible.get(calendar_id, None if calendar_id == "primary" else calendar_id))
                for calendar_id in self.config.calendar.calendar_ids
            ]
        default_calendar_id = self._default_calendar_id()
        return [(default_calendar_id, None if default_calendar_id == "primary" else default_calendar_id)]

    def _list_accessible_calendars_google(self, service) -> list[tuple[str, str | None]]:
        """Return visible calendar ids and names for the authenticated principal."""

        calendars: list[tuple[str, str | None]] = []
        page_token = None
        while True:
            response = service.calendarList().list(pageToken=page_token).execute()
            for item in response.get("items", []):
                if not isinstance(item, dict):
                    continue
                calendar_id = item.get("id")
                if not isinstance(calendar_id, str) or not calendar_id:
                    continue
                calendars.append((calendar_id, item.get("summary")))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return calendars or [("primary", None)]

    def _default_calendar_id(self) -> str:
        """Return the configured default calendar id used for event creation."""

        if self.config.calendar.default_calendar_id:
            return self.config.calendar.default_calendar_id
        if self.config.calendar.calendar_ids:
            return self.config.calendar.calendar_ids[0]
        return "primary"

    def _from_google_item(
        self,
        item: dict[str, Any],
        *,
        default_calendar_id: str,
        default_calendar_name: str | None,
    ) -> CalendarEvent:
        """Normalize one Google Calendar API event resource."""

        organizer = item.get("organizer", {}) if isinstance(item.get("organizer"), dict) else {}
        event_id = item.get("id") or uuid.uuid4().hex
        summary = item.get("summary") or "(untitled event)"
        start_info = item.get("start", {})
        end_info = item.get("end", {})
        start = _normalize_google_time(start_info)
        end = _normalize_google_time(end_info)
        return CalendarEvent(
            event_id=str(event_id),
            calendar_id=str(organizer.get("email") or default_calendar_id),
            calendar_name=organizer.get("displayName") or default_calendar_name,
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
