"""Shared persistent conversation state and controller logic for Nyx overlays."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import textwrap
from uuid import uuid4

from nyx.bridges.base import SystemBridge, WindowInfo
from nyx.config import NyxConfig
from nyx.daemon import NyxDaemon
from nyx.intent_router import IntentRequest, IntentResult
from nyx.providers.base import ProviderMessage
from nyx.ui.history_store import (
    OverlayHistorySnapshot,
    OverlayHistoryStore,
    StoredConversation,
    StoredConversationMessage,
)

_MAX_ROUTED_HISTORY_MESSAGES = 8
_MAX_ROUTED_HISTORY_CHARS = 12_000


@dataclass(slots=True)
class OverlayViewState:
    """UI state rendered by Nyx overlay windows."""

    response_text: str = "Nyx is ready."
    conversation_text: str = "## Assistant\n\nNyx is ready."
    provider_name: str = "idle"
    model_name: str | None = None
    token_count: int | None = None
    degraded: bool = False
    yolo: bool = False
    busy: bool = False
    active_window: WindowInfo | None = None
    selected_session_id: str | None = None


@dataclass(slots=True)
class ConversationMessage:
    """One user or assistant message inside a persisted UI conversation."""

    role: str
    text: str
    created_at: datetime
    provider_name: str | None = None
    model_name: str | None = None
    token_count: int | None = None


@dataclass(slots=True)
class SessionRecord:
    """One persisted overlay conversation shown in the history sidebar."""

    session_id: str
    created_at: datetime
    updated_at: datetime
    active_window: WindowInfo | None
    degraded: bool
    summary: str | None
    archived: bool
    pinned: bool
    messages: list[ConversationMessage]

    @property
    def prompt(self) -> str:
        """Return the latest user prompt for input restoration."""

        for message in reversed(self.messages):
            if message.role == "user":
                return message.text
        return ""

    @property
    def response_text(self) -> str:
        """Return the latest assistant response text."""

        for message in reversed(self.messages):
            if message.role == "assistant":
                return message.text
        return "Nyx is ready."

    @property
    def provider_name(self) -> str:
        """Return the provider name used for the latest assistant response."""

        for message in reversed(self.messages):
            if message.role == "assistant" and message.provider_name:
                return message.provider_name
        return "idle"

    @property
    def model_name(self) -> str | None:
        """Return the latest assistant model name, if known."""

        for message in reversed(self.messages):
            if message.role == "assistant" and message.model_name:
                return message.model_name
        return None

    @property
    def token_count(self) -> int | None:
        """Return the latest assistant token count, if known."""

        for message in reversed(self.messages):
            if message.role == "assistant" and message.token_count is not None:
                return message.token_count
        return None

    @property
    def title(self) -> str:
        """Return a stable conversation title derived from the first prompt."""

        first_prompt = next((message.text for message in self.messages if message.role == "user"), "")
        if not first_prompt:
            return "Untitled conversation"
        return textwrap.shorten(" ".join(first_prompt.split()), width=46, placeholder="…")

    @property
    def subtitle(self) -> str:
        """Return a human-readable timestamp label for the sidebar."""

        now = datetime.now(self.updated_at.tzinfo)
        if self.updated_at.date() == now.date():
            return self.updated_at.strftime("Today %H:%M")
        return self.updated_at.strftime("%Y-%m-%d %H:%M")

    @property
    def preview(self) -> str:
        """Return a one-line preview from the latest assistant or user message."""

        latest = self.response_text or self.prompt
        return textwrap.shorten(" ".join(latest.split()), width=88, placeholder="…")

    @property
    def search_text(self) -> str:
        """Return normalized search text used for sidebar filtering."""

        parts = [
            self.title,
            self.summary or "",
            self.provider_name,
            self.model_name or "",
            self.active_window.app_name if self.active_window else "",
            self.active_window.window_title if self.active_window else "",
        ]
        parts.extend(message.text for message in self.messages)
        return " ".join(part for part in parts if part).casefold()

    @property
    def document_markdown(self) -> str:
        """Return the conversation as a markdown-like document transcript."""

        blocks: list[str] = []
        for message in self.messages:
            speaker = "User" if message.role == "user" else "Assistant"
            blocks.append(f"## {speaker}\n\n{message.text}")
        return "\n\n".join(blocks).strip() or "## Assistant\n\nNyx is ready."


@dataclass(slots=True)
class OverlaySessionController:
    """Manage prompt submission, persistent conversations, and UI status mapping."""

    daemon: NyxDaemon
    bridge: SystemBridge
    config: NyxConfig
    logger: logging.Logger
    history_store: OverlayHistoryStore = field(default_factory=OverlayHistoryStore)
    history: list[str] = field(default_factory=list)
    sessions: list[SessionRecord] = field(default_factory=list)
    selected_session_id: str | None = None
    _history_index: int | None = None

    def __post_init__(self) -> None:
        """Load persisted conversations into memory for the overlay."""

        snapshot = self.history_store.load()
        self.history = list(snapshot.prompt_history)
        self.sessions = [self._session_from_stored(conversation) for conversation in snapshot.conversations]
        if self.sessions:
            self.selected_session_id = self.sessions[-1].session_id

    async def submit_prompt(self, prompt: str, model_override: str | None = None) -> OverlayViewState:
        """Submit a prompt, append it to the current conversation, and persist it."""

        active_window = await self._safe_active_window()
        target_session = self._selected_session_for_submission()
        conversation_messages = None
        if target_session is not None:
            conversation_messages = self._build_conversation_messages(target_session, prompt)

        result = await self.daemon.handle_prompt(
            IntentRequest(
                text=prompt,
                model_override=model_override,
                yolo=self.config.system.yolo,
                conversation_messages=conversation_messages,
            )
        )
        self._record_history(prompt)
        record = self._record_session(prompt, result, active_window, existing_session=target_session)
        self._persist()
        return self._state_from_session(record)

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
        """Return the overlay state shown before any prompt is submitted."""

        if self.selected_session_id is not None:
            selected = self.state_for_session(self.selected_session_id)
            if selected is not None:
                return selected
        return OverlayViewState(
            response_text="Nyx is ready.",
            conversation_text="## Assistant\n\nNyx is ready.",
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

        selected = self.get_session(self.selected_session_id) if self.selected_session_id is not None else None
        pending_doc = (
            f"{selected.document_markdown}\n\n## Assistant\n\nThinking…"
            if selected is not None
            else "## Assistant\n\nThinking…"
        )
        return OverlayViewState(
            response_text="Thinking…",
            conversation_text=pending_doc,
            provider_name=selected.provider_name if selected else self.config.models.default,
            model_name=selected.model_name if selected else None,
            token_count=selected.token_count if selected else None,
            degraded=selected.degraded if selected else False,
            yolo=self.config.system.yolo,
            busy=True,
            active_window=selected.active_window if selected else None,
            selected_session_id=self.selected_session_id,
        )

    def filter_sessions(self, query: str) -> list[SessionRecord]:
        """Return conversations filtered by the given search query."""

        normalized = query.strip().casefold()
        candidates = [session for session in self.sessions if not session.archived]
        if not normalized:
            return list(reversed(candidates))
        return [
            session
            for session in reversed(candidates)
            if normalized in session.search_text
        ]

    def get_session(self, session_id: str | None) -> SessionRecord | None:
        """Return a conversation record by identifier, if present."""

        if session_id is None:
            return None
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        return None

    def state_for_session(self, session_id: str) -> OverlayViewState | None:
        """Return an overlay state reconstructed from a stored conversation."""

        session = self.get_session(session_id)
        if session is None:
            return None
        self.selected_session_id = session_id
        return self._state_from_session(session)

    def start_new_conversation(self) -> OverlayViewState:
        """Clear the selected conversation so the next prompt starts a new thread."""

        self.selected_session_id = None
        return self.idle_state()

    def delete_session(self, session_id: str) -> OverlayViewState:
        """Delete a conversation and return the best next idle/selected state."""

        self.sessions = [session for session in self.sessions if session.session_id != session_id]
        if self.selected_session_id == session_id:
            self.selected_session_id = self.sessions[-1].session_id if self.sessions else None
        self._persist()
        return self.idle_state()

    def archive_session(self, session_id: str) -> OverlayViewState:
        """Archive a conversation and move selection to the latest active thread."""

        session = self.get_session(session_id)
        if session is not None:
            session.archived = True
        if self.selected_session_id == session_id:
            active_sessions = [item for item in self.sessions if not item.archived]
            self.selected_session_id = active_sessions[-1].session_id if active_sessions else None
        self._persist()
        return self.idle_state()

    async def _safe_active_window(self) -> WindowInfo | None:
        """Fetch the active window without failing the whole request on error."""

        try:
            return await self.bridge.get_active_window()
        except Exception:
            self.logger.exception("Failed to refresh active window for overlay status.")
            return None

    def _selected_session_for_submission(self) -> SessionRecord | None:
        """Return the selected conversation reused for the next prompt, if any."""

        return self.get_session(self.selected_session_id)

    def _record_history(self, prompt: str) -> None:
        """Append a prompt to history and reset traversal state."""

        normalized = prompt.strip()
        if normalized and (not self.history or self.history[-1] != normalized):
            self.history.append(normalized)
        self._history_index = None

    def _record_session(
        self,
        prompt: str,
        result: IntentResult,
        active_window: WindowInfo | None,
        existing_session: SessionRecord | None = None,
    ) -> SessionRecord:
        """Append a prompt/result pair to an existing or new conversation."""

        now = datetime.now().astimezone()
        user_message = ConversationMessage(role="user", text=prompt, created_at=now)
        assistant_message = ConversationMessage(
            role="assistant",
            text=result.response_text,
            created_at=now,
            provider_name=result.used_model or self.config.models.default,
            model_name=result.model_name,
            token_count=result.token_count,
        )
        if existing_session is None:
            record = SessionRecord(
                session_id=uuid4().hex,
                created_at=now,
                updated_at=now,
                active_window=active_window,
                degraded=result.degraded,
                summary=None,
                archived=False,
                pinned=False,
                messages=[user_message, assistant_message],
            )
            self.sessions.append(record)
        else:
            existing_session.messages.extend([user_message, assistant_message])
            existing_session.updated_at = now
            existing_session.active_window = active_window
            existing_session.degraded = existing_session.degraded or result.degraded
            existing_session.summary = textwrap.shorten(
                " ".join(existing_session.response_text.split()),
                width=140,
                placeholder="…",
            )
            record = existing_session

        self.selected_session_id = record.session_id
        return record

    def _state_from_session(self, session: SessionRecord) -> OverlayViewState:
        """Map a stored conversation back into the view state used by GTK."""

        return OverlayViewState(
            response_text=session.response_text,
            conversation_text=session.document_markdown,
            provider_name=session.provider_name,
            model_name=session.model_name,
            token_count=session.token_count,
            degraded=session.degraded,
            yolo=self.config.system.yolo,
            busy=False,
            active_window=session.active_window,
            selected_session_id=session.session_id,
        )

    def _build_conversation_messages(
        self,
        session: SessionRecord,
        prompt: str,
    ) -> list[ProviderMessage]:
        """Build a bounded structured conversation window for provider routing."""

        routed_messages: list[ProviderMessage] = []
        remaining_chars = _MAX_ROUTED_HISTORY_CHARS
        for message in reversed(session.messages[-_MAX_ROUTED_HISTORY_MESSAGES:]):
            text = message.text.strip()
            if not text:
                continue
            if remaining_chars <= 0:
                break
            if len(text) > remaining_chars:
                text = text[-remaining_chars:]
            routed_messages.append(ProviderMessage(role=message.role, content=text))
            remaining_chars -= len(text)
        routed_messages.reverse()
        routed_messages.append(ProviderMessage(role="user", content=prompt.strip()))
        return routed_messages

    def _persist(self) -> None:
        """Persist prompt history and current conversations to local storage."""

        snapshot = OverlayHistorySnapshot(
            prompt_history=list(self.history),
            conversations=[self._stored_from_session(session) for session in self.sessions],
        )
        self.history_store.save(snapshot)

    def _session_from_stored(self, conversation: StoredConversation) -> SessionRecord:
        """Convert one stored conversation into the runtime session model."""

        return SessionRecord(
            session_id=conversation.conversation_id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            active_window=conversation.active_window,
            degraded=conversation.degraded,
            summary=conversation.summary,
            archived=conversation.archived,
            pinned=conversation.pinned,
            messages=[
                ConversationMessage(
                    role=message.role,
                    text=message.text,
                    created_at=message.created_at,
                    provider_name=message.provider_name,
                    model_name=message.model_name,
                    token_count=message.token_count,
                )
                for message in conversation.messages
            ],
        )

    def _stored_from_session(self, session: SessionRecord) -> StoredConversation:
        """Convert one runtime session into the serialized store model."""

        return StoredConversation(
            conversation_id=session.session_id,
            created_at=session.created_at,
            updated_at=session.updated_at,
            active_window=session.active_window,
            degraded=session.degraded,
            summary=session.summary,
            provider_name=session.provider_name,
            model_name=session.model_name,
            archived=session.archived,
            pinned=session.pinned,
            messages=[
                StoredConversationMessage(
                    role=message.role,
                    text=message.text,
                    created_at=message.created_at,
                    provider_name=message.provider_name,
                    model_name=message.model_name,
                    token_count=message.token_count,
                )
                for message in session.messages
            ],
        )
