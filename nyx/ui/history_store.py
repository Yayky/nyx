"""Persistent local conversation storage for the Nyx overlay UI.

The overlay keeps a local-first JSON store so sidebar history survives across
launcher runs without introducing a database dependency. The stored structure is
intentionally simple: prompt history plus threaded conversations with message
lists and lightweight metadata for search and rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import tempfile
from typing import Any

from nyx.bridges.base import WindowInfo

DEFAULT_OVERLAY_HISTORY_PATH = Path("~/.local/state/nyx/conversations.json").expanduser()


@dataclass(slots=True)
class StoredConversationMessage:
    """One persisted user or assistant message inside a conversation thread."""

    role: str
    text: str
    created_at: datetime
    provider_name: str | None = None
    model_name: str | None = None
    token_count: int | None = None


@dataclass(slots=True)
class StoredConversation:
    """A persisted conversation entry loaded by the overlay controller."""

    conversation_id: int
    created_at: datetime
    updated_at: datetime
    active_window: WindowInfo | None
    degraded: bool
    messages: list[StoredConversationMessage]


@dataclass(slots=True)
class OverlayHistorySnapshot:
    """Full persisted overlay history snapshot."""

    prompt_history: list[str]
    conversations: list[StoredConversation]


class OverlayHistoryStore:
    """Read and write the overlay conversation history on local disk."""

    def __init__(self, path: Path | None = None) -> None:
        """Store the history path used for later load/save operations."""

        self.path = (path or DEFAULT_OVERLAY_HISTORY_PATH).expanduser()

    def load(self) -> OverlayHistorySnapshot:
        """Load prompt history and conversations from disk.

        Invalid or missing files are treated as empty history so the launcher
        stays usable even if local UI state becomes corrupted.
        """

        if not self.path.exists():
            return OverlayHistorySnapshot(prompt_history=[], conversations=[])

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return OverlayHistorySnapshot(prompt_history=[], conversations=[])

        prompt_history = [
            prompt
            for prompt in raw.get("prompt_history", [])
            if isinstance(prompt, str) and prompt.strip()
        ]
        conversations: list[StoredConversation] = []
        for item in raw.get("conversations", []):
            conversation = self._parse_conversation(item)
            if conversation is not None:
                conversations.append(conversation)
        return OverlayHistorySnapshot(prompt_history=prompt_history, conversations=conversations)

    def save(self, snapshot: OverlayHistorySnapshot) -> None:
        """Persist the supplied prompt history and conversation list atomically."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "prompt_history": snapshot.prompt_history,
            "conversations": [self._serialize_conversation(conversation) for conversation in snapshot.conversations],
        }
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".nyx-history-",
            delete=False,
        ) as temp_file:
            json.dump(payload, temp_file, indent=2, ensure_ascii=True)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        temp_path.replace(self.path)

    def _parse_conversation(self, item: Any) -> StoredConversation | None:
        """Parse one serialized conversation record from JSON-compatible data."""

        if not isinstance(item, dict):
            return None
        messages: list[StoredConversationMessage] = []
        for raw_message in item.get("messages", []):
            message = self._parse_message(raw_message)
            if message is not None:
                messages.append(message)
        if not messages:
            return None

        try:
            created_at = datetime.fromisoformat(str(item["created_at"]))
            updated_at = datetime.fromisoformat(str(item.get("updated_at", item["created_at"])))
            conversation_id = int(item["conversation_id"])
        except (KeyError, TypeError, ValueError):
            return None

        active_window = self._parse_window(item.get("active_window"))
        return StoredConversation(
            conversation_id=conversation_id,
            created_at=created_at,
            updated_at=updated_at,
            active_window=active_window,
            degraded=bool(item.get("degraded", False)),
            messages=messages,
        )

    def _parse_message(self, raw_message: Any) -> StoredConversationMessage | None:
        """Parse one serialized conversation message from JSON-compatible data."""

        if not isinstance(raw_message, dict):
            return None
        try:
            role = str(raw_message["role"])
            text = str(raw_message["text"])
            created_at = datetime.fromisoformat(str(raw_message["created_at"]))
        except (KeyError, TypeError, ValueError):
            return None
        if role not in {"user", "assistant"} or not text.strip():
            return None
        return StoredConversationMessage(
            role=role,
            text=text,
            created_at=created_at,
            provider_name=_optional_string(raw_message.get("provider_name")),
            model_name=_optional_string(raw_message.get("model_name")),
            token_count=_optional_int(raw_message.get("token_count")),
        )

    def _parse_window(self, raw_window: Any) -> WindowInfo | None:
        """Parse a serialized active-window payload into ``WindowInfo``."""

        if not isinstance(raw_window, dict):
            return None
        app_name = _optional_string(raw_window.get("app_name"))
        window_title = _optional_string(raw_window.get("window_title"))
        workspace = _optional_string(raw_window.get("workspace"))
        if app_name is None and window_title is None and workspace is None:
            return None
        return WindowInfo(
            app_name=app_name or "",
            window_title=window_title or "",
            workspace=workspace or "",
        )

    def _serialize_conversation(self, conversation: StoredConversation) -> dict[str, Any]:
        """Serialize one conversation for JSON persistence."""

        return {
            "conversation_id": conversation.conversation_id,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
            "degraded": conversation.degraded,
            "active_window": _serialize_window(conversation.active_window),
            "messages": [
                {
                    "role": message.role,
                    "text": message.text,
                    "created_at": message.created_at.isoformat(),
                    "provider_name": message.provider_name,
                    "model_name": message.model_name,
                    "token_count": message.token_count,
                }
                for message in conversation.messages
            ],
        }


def _serialize_window(active_window: WindowInfo | None) -> dict[str, str] | None:
    """Serialize ``WindowInfo`` into a JSON-compatible mapping."""

    if active_window is None:
        return None
    return {
        "app_name": active_window.app_name,
        "window_title": active_window.window_title,
        "workspace": active_window.workspace,
    }


def _optional_string(value: Any) -> str | None:
    """Return a stripped optional string or ``None``."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    """Return an optional integer from JSON-compatible data."""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
