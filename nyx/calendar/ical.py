"""Minimal `.ical` cache support for Nyx calendar fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class CachedCalendarEvent:
    """One calendar event stored in or loaded from the local `.ical` cache."""

    event_id: str
    summary: str
    start: str
    end: str
    location: str | None
    description: str | None


class IcalCache:
    """Read and write a small `.ical` cache used for offline calendar fallback."""

    def __init__(self, cache_path: Path) -> None:
        """Store the cache location used by the calendar service."""

        self.cache_path = cache_path

    async def write_events(self, events: Iterable[CachedCalendarEvent]) -> None:
        """Write one complete VCALENDAR file with the supplied events."""

        def _sync_write() -> None:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Nyx//Calendar Cache//EN",
            ]
            for event in events:
                lines.extend(
                    [
                        "BEGIN:VEVENT",
                        f"UID:{_escape_text(event.event_id)}",
                        f"SUMMARY:{_escape_text(event.summary)}",
                        f"DTSTART:{_to_ical_timestamp(event.start)}",
                        f"DTEND:{_to_ical_timestamp(event.end)}",
                    ]
                )
                if event.location:
                    lines.append(f"LOCATION:{_escape_text(event.location)}")
                if event.description:
                    lines.append(f"DESCRIPTION:{_escape_text(event.description)}")
                lines.append("END:VEVENT")
            lines.append("END:VCALENDAR")
            self.cache_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

        await asyncio.to_thread(_sync_write)

    async def read_events(self) -> list[CachedCalendarEvent]:
        """Read cached events from disk, returning an empty list when absent."""

        def _sync_read() -> list[CachedCalendarEvent]:
            if not self.cache_path.exists():
                return []

            content = self.cache_path.read_text(encoding="utf-8")
            events: list[CachedCalendarEvent] = []
            current: dict[str, str] | None = None

            for raw_line in content.splitlines():
                line = raw_line.strip()
                if line == "BEGIN:VEVENT":
                    current = {}
                    continue
                if line == "END:VEVENT":
                    if current is not None:
                        events.append(
                            CachedCalendarEvent(
                                event_id=current.get("UID", ""),
                                summary=_unescape_text(current.get("SUMMARY", "")),
                                start=_from_ical_timestamp(current.get("DTSTART", "")),
                                end=_from_ical_timestamp(current.get("DTEND", "")),
                                location=_none_if_empty(_unescape_text(current.get("LOCATION", ""))),
                                description=_none_if_empty(_unescape_text(current.get("DESCRIPTION", ""))),
                            )
                        )
                    current = None
                    continue
                if current is None or ":" not in line:
                    continue

                key, value = line.split(":", 1)
                current[key] = value

            return [event for event in events if event.summary and event.start and event.end]

        return await asyncio.to_thread(_sync_read)


def _to_ical_timestamp(value: str) -> str:
    """Convert an ISO datetime string into a UTC iCalendar timestamp."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def _from_ical_timestamp(value: str) -> str:
    """Convert a UTC iCalendar timestamp into an ISO datetime string."""

    if not value:
        return ""
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return parsed.isoformat()


def _escape_text(value: str) -> str:
    """Escape plain text for simple iCalendar storage."""

    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", r"\,").replace(";", r"\;")


def _unescape_text(value: str) -> str:
    """Unescape plain text read from the simple iCalendar cache."""

    return value.replace(r"\n", "\n").replace(r"\,", ",").replace(r"\;", ";").replace("\\\\", "\\")


def _none_if_empty(value: str) -> str | None:
    """Return ``None`` for empty strings when reading optional event fields."""

    return value or None
