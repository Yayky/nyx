"""Shared UI session state and controller logic for Nyx overlay windows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import textwrap

from nyx.bridges.base import SystemBridge, WindowInfo
from nyx.config import NyxConfig
from nyx.daemon import NyxDaemon
from nyx.intent_router import IntentRequest, IntentResult


@dataclass(slots=True)
class OverlayViewState:
    """UI state rendered by Nyx overlay windows."""

    response_text: str = "Nyx launcher ready."
    provider_name: str = "idle"
    model_name: str | None = None
    token_count: int | None = None
    degraded: bool = False
    yolo: bool = False
    busy: bool = False
    active_window: WindowInfo | None = None
    selected_session_id: int | None = None


@dataclass(slots=True)
class SessionRecord:
    """A single in-memory overlay session entry used by panel history."""

    session_id: int
    prompt: str
    response_text: str
    provider_name: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    created_at: datetime
    active_window: WindowInfo | None

    @property
    def title(self) -> str:
        """Return the panel title shown for this session."""

        return f"Session {self.session_id}"

    @property
    def subtitle(self) -> str:
        """Return a short timestamp label for sidebar display."""

        now = datetime.now(self.created_at.tzinfo)
        if self.created_at.date() == now.date():
            return self.created_at.strftime("Today %H:%M")
        return self.created_at.strftime("%Y-%m-%d %H:%M")

    @property
    def preview(self) -> str:
        """Return a one-line preview derived from the prompt."""

        return textwrap.shorten(" ".join(self.prompt.split()), width=72, placeholder="…")

    @property
    def search_text(self) -> str:
        """Return the normalized search text for panel filtering."""

        return " ".join(
            part
            for part in (
                self.prompt,
                self.response_text,
                self.provider_name,
                self.model_name or "",
                self.active_window.app_name if self.active_window else "",
                self.active_window.window_title if self.active_window else "",
            )
            if part
        ).casefold()


@dataclass(slots=True)
class OverlaySessionController:
    """Manage prompt submission, history, sessions, and status mapping."""

    daemon: NyxDaemon
    bridge: SystemBridge
    config: NyxConfig
    logger: logging.Logger
    history: list[str] = field(default_factory=list)
    sessions: list[SessionRecord] = field(default_factory=list)
    selected_session_id: int | None = None
    _history_index: int | None = None

    async def submit_prompt(self, prompt: str, model_override: str | None = None) -> OverlayViewState:
        """Submit a prompt through the daemon and store a session entry."""

        result = await self.daemon.handle_prompt(
            IntentRequest(
                text=prompt,
                model_override=model_override,
                yolo=self.config.system.yolo,
            )
        )
        self._record_history(prompt)
        active_window = await self._safe_active_window()
        record = self._record_session(prompt, result, active_window)
        return self._state_from_result(result, active_window, selected_session_id=record.session_id)

    def previous_history(self) -> str | None:
        """Return the previous prompt from session history, if available."""

        if not self.history:
            return None
        if self._history_index is None:
            self._history_index = len(self.history) - 1
        else:
            self._history_index = max(0, self._history_index - 1)
        return self.history[self._history_index]

    def next_history(self) -> str:
        """Return the next prompt from history or an empty string at the end."""

        if not self.history or self._history_index is None:
            return ""
        self._history_index += 1
        if self._history_index >= len(self.history):
            self._history_index = None
            return ""
        return self.history[self._history_index]

    def idle_state(self) -> OverlayViewState:
        """Return the initial overlay state before any prompt is submitted."""

        return OverlayViewState(
            response_text="Nyx launcher ready.",
            provider_name=self.config.models.default,
            model_name=None,
            token_count=None,
            degraded=False,
            yolo=self.config.system.yolo,
            busy=False,
            active_window=None,
            selected_session_id=self.selected_session_id,
        )

    def busy_state(self) -> OverlayViewState:
        """Return the temporary state shown while a prompt is in flight."""

        return OverlayViewState(
            response_text="Thinking…",
            provider_name=self.config.models.default,
            model_name=None,
            token_count=None,
            degraded=False,
            yolo=self.config.system.yolo,
            busy=True,
            active_window=None,
            selected_session_id=self.selected_session_id,
        )

    def filter_sessions(self, query: str) -> list[SessionRecord]:
        """Return panel sessions filtered by the given search query."""

        normalized = query.strip().casefold()
        if not normalized:
            return list(reversed(self.sessions))
        return [
            session
            for session in reversed(self.sessions)
            if normalized in session.search_text
        ]

    def get_session(self, session_id: int) -> SessionRecord | None:
        """Return a session record by identifier, if present."""

        for session in self.sessions:
            if session.session_id == session_id:
                return session
        return None

    def state_for_session(self, session_id: int) -> OverlayViewState | None:
        """Return an overlay state reconstructed from a session record."""

        session = self.get_session(session_id)
        if session is None:
            return None
        self.selected_session_id = session_id
        return OverlayViewState(
            response_text=session.response_text,
            provider_name=session.provider_name,
            model_name=session.model_name,
            token_count=session.token_count,
            degraded=session.degraded,
            yolo=self.config.system.yolo,
            busy=False,
            active_window=session.active_window,
            selected_session_id=session.session_id,
        )

    async def _safe_active_window(self) -> WindowInfo | None:
        """Fetch the active window without failing the whole request on error."""

        try:
            return await self.bridge.get_active_window()
        except Exception:
            self.logger.exception("Failed to refresh active window for overlay status.")
            return None

    def _record_history(self, prompt: str) -> None:
        """Append a prompt to history and reset history traversal state."""

        normalized = prompt.strip()
        if normalized and (not self.history or self.history[-1] != normalized):
            self.history.append(normalized)
        self._history_index = None

    def _record_session(
        self,
        prompt: str,
        result: IntentResult,
        active_window: WindowInfo | None,
    ) -> SessionRecord:
        """Append a panel session entry derived from a completed prompt."""

        record = SessionRecord(
            session_id=len(self.sessions) + 1,
            prompt=prompt,
            response_text=result.response_text,
            provider_name=result.used_model or self.config.models.default,
            model_name=result.model_name,
            token_count=result.token_count,
            degraded=result.degraded,
            created_at=datetime.now().astimezone(),
            active_window=active_window,
        )
        self.sessions.append(record)
        self.selected_session_id = record.session_id
        return record

    def _state_from_result(
        self,
        result: IntentResult,
        active_window: WindowInfo | None,
        selected_session_id: int | None,
    ) -> OverlayViewState:
        """Convert a router result into the view state consumed by overlay windows."""

        return OverlayViewState(
            response_text=result.response_text,
            provider_name=result.used_model or self.config.models.default,
            model_name=result.model_name,
            token_count=result.token_count,
            degraded=result.degraded,
            yolo=self.config.system.yolo,
            busy=False,
            active_window=active_window,
            selected_session_id=selected_session_id,
        )
