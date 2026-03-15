"""Persistent local conversation storage for the Nyx overlay UI.

The overlay now keeps its local-first history in SQLite so threaded
conversations survive across launcher runs without silent truncation and with a
clean upgrade path toward richer backends later. A legacy JSON store is still
recognized once as a migration source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

from nyx.bridges.base import WindowInfo

DEFAULT_OVERLAY_HISTORY_PATH = Path("~/.local/state/nyx/conversations.db").expanduser()
LEGACY_OVERLAY_HISTORY_PATH = Path("~/.local/state/nyx/conversations.json").expanduser()


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

    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    active_window: WindowInfo | None
    degraded: bool
    summary: str | None
    provider_name: str | None
    model_name: str | None
    archived: bool
    pinned: bool
    messages: list[StoredConversationMessage]


@dataclass(slots=True)
class OverlayHistorySnapshot:
    """Full persisted overlay history snapshot."""

    prompt_history: list[str]
    conversations: list[StoredConversation]


class OverlayHistoryStore:
    """Read and write the overlay conversation history on local disk."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        legacy_json_path: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Store the SQLite history path used for later load/save operations."""

        resolved_path = (path or DEFAULT_OVERLAY_HISTORY_PATH).expanduser()
        if resolved_path.suffix == ".json":
            self.path = resolved_path.with_suffix(".db")
            self.legacy_json_path = resolved_path
        else:
            self.path = resolved_path
            self.legacy_json_path = (legacy_json_path or LEGACY_OVERLAY_HISTORY_PATH).expanduser()
        self.logger = logger or logging.getLogger("nyx.ui.history")
        self._initialized = False

    def load(self) -> OverlayHistorySnapshot:
        """Load prompt history and conversations from disk.

        Invalid or missing files are treated as empty history so the launcher
        stays usable even if local UI state becomes corrupted.
        """

        self._ensure_initialized()
        with self._connect() as connection:
            prompt_history = [
                str(row["prompt"])
                for row in connection.execute(
                    "SELECT prompt FROM prompt_history ORDER BY id ASC"
                ).fetchall()
                if str(row["prompt"]).strip()
            ]
            conversations = self._load_conversations(connection)
        return OverlayHistorySnapshot(prompt_history=prompt_history, conversations=conversations)

    def save(self, snapshot: OverlayHistorySnapshot) -> None:
        """Persist the supplied prompt history and conversation list atomically."""

        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM prompt_history")
            connection.executemany(
                "INSERT INTO prompt_history(prompt, created_at) VALUES(?, ?)",
                [
                    (prompt, datetime.now().astimezone().isoformat())
                    for prompt in snapshot.prompt_history
                    if prompt.strip()
                ],
            )
            connection.execute("DELETE FROM messages")
            connection.execute("DELETE FROM threads")
            for conversation in snapshot.conversations:
                connection.execute(
                    """
                    INSERT INTO threads(
                        id,
                        title,
                        created_at,
                        updated_at,
                        archived,
                        pinned,
                        summary,
                        provider_name,
                        model_name,
                        active_window_app,
                        active_window_title,
                        active_window_workspace,
                        degraded
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation.conversation_id,
                        conversation.title or _conversation_title(conversation),
                        conversation.created_at.isoformat(),
                        conversation.updated_at.isoformat(),
                        int(conversation.archived),
                        int(conversation.pinned),
                        conversation.summary,
                        conversation.provider_name,
                        conversation.model_name,
                        conversation.active_window.app_name if conversation.active_window else None,
                        conversation.active_window.window_title if conversation.active_window else None,
                        conversation.active_window.workspace if conversation.active_window else None,
                        int(conversation.degraded),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO messages(
                        id,
                        thread_id,
                        role,
                        text,
                        created_at,
                        token_count,
                        provider_name,
                        model_name
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            f"{conversation.conversation_id}:{index}",
                            conversation.conversation_id,
                            message.role,
                            message.text,
                            message.created_at.isoformat(),
                            message.token_count,
                            message.provider_name,
                            message.model_name,
                        )
                        for index, message in enumerate(conversation.messages, start=1)
                    ],
                )
            connection.commit()

    def delete_thread(self, conversation_id: str) -> None:
        """Delete one conversation thread and its messages."""

        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM messages WHERE thread_id = ?", (conversation_id,))
            connection.execute("DELETE FROM threads WHERE id = ?", (conversation_id,))
            connection.commit()

    def archive_thread(self, conversation_id: str, archived: bool = True) -> None:
        """Mark one conversation thread as archived or active."""

        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute(
                "UPDATE threads SET archived = ?, updated_at = ? WHERE id = ?",
                (int(archived), datetime.now().astimezone().isoformat(), conversation_id),
            )
            connection.commit()

    def _ensure_initialized(self) -> None:
        """Create the SQLite schema and migrate the legacy JSON store once."""

        if self._initialized:
            return

        should_migrate = not self.path.exists() and self.legacy_json_path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        if should_migrate:
            self._migrate_legacy_json()
        else:
            # Ensure the DB file exists even with empty history.
            with self._connect():
                pass
        self._initialized = True

    def _initialize_schema(self) -> None:
        """Create the database schema when it does not already exist."""

        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    summary TEXT,
                    provider_name TEXT,
                    model_name TEXT,
                    active_window_app TEXT,
                    active_window_title TEXT,
                    active_window_workspace TEXT,
                    degraded INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    token_count INTEGER,
                    provider_name TEXT,
                    model_name TEXT,
                    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS prompt_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_thread_created
                    ON messages(thread_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_threads_updated_at
                    ON threads(updated_at DESC);
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        """Open one SQLite connection with row access enabled."""

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _load_conversations(self, connection: sqlite3.Connection) -> list[StoredConversation]:
        """Load all conversation threads and attached messages from SQLite."""

        threads = connection.execute(
            """
            SELECT
                id,
                title,
                created_at,
                updated_at,
                archived,
                pinned,
                summary,
                provider_name,
                model_name,
                active_window_app,
                active_window_title,
                active_window_workspace,
                degraded
            FROM threads
            ORDER BY updated_at ASC, created_at ASC
            """
        ).fetchall()
        conversations: list[StoredConversation] = []
        for row in threads:
            message_rows = connection.execute(
                """
                SELECT role, text, created_at, provider_name, model_name, token_count
                FROM messages
                WHERE thread_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (row["id"],),
            ).fetchall()
            messages = [
                StoredConversationMessage(
                    role=str(message_row["role"]),
                    text=str(message_row["text"]),
                    created_at=datetime.fromisoformat(str(message_row["created_at"])),
                    provider_name=_optional_string(message_row["provider_name"]),
                    model_name=_optional_string(message_row["model_name"]),
                    token_count=_optional_int(message_row["token_count"]),
                )
                for message_row in message_rows
                if str(message_row["role"]) in {"user", "assistant"} and str(message_row["text"]).strip()
            ]
            if not messages:
                continue
            conversations.append(
                StoredConversation(
                    conversation_id=str(row["id"]),
                    title=_optional_string(row["title"]) or _conversation_title_from_messages(messages),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                    active_window=_window_from_columns(row),
                    degraded=bool(row["degraded"]),
                    summary=_optional_string(row["summary"]),
                    provider_name=_optional_string(row["provider_name"]),
                    model_name=_optional_string(row["model_name"]),
                    archived=bool(row["archived"]),
                    pinned=bool(row["pinned"]),
                    messages=messages,
                )
            )
        return conversations

    def _migrate_legacy_json(self) -> None:
        """Import the legacy JSON history file into SQLite once."""

        snapshot = self._load_legacy_snapshot(self.legacy_json_path)
        if snapshot is None:
            self.logger.warning(
                "Nyx overlay history migration skipped because the legacy file is unreadable: %s",
                self.legacy_json_path,
            )
            return

        self.save(snapshot)
        backup_path = self.legacy_json_path.with_suffix(self.legacy_json_path.suffix + ".migrated.bak")
        try:
            self.legacy_json_path.replace(backup_path)
        except OSError as exc:
            self.logger.warning(
                "Nyx migrated legacy overlay history but could not rename %s: %s",
                self.legacy_json_path,
                exc,
            )
            return
        self.logger.info(
            "Migrated legacy overlay history from %s to %s",
            self.legacy_json_path,
            self.path,
        )

    def _load_legacy_snapshot(self, legacy_path: Path) -> OverlayHistorySnapshot | None:
        """Load one legacy JSON snapshot for migration."""

        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        prompt_history = [
            prompt
            for prompt in raw.get("prompt_history", [])
            if isinstance(prompt, str) and prompt.strip()
        ]
        conversations: list[StoredConversation] = []
        for item in raw.get("conversations", []):
            conversation = _parse_legacy_conversation(item)
            if conversation is not None:
                conversations.append(conversation)
        return OverlayHistorySnapshot(prompt_history=prompt_history, conversations=conversations)


def _parse_legacy_conversation(item: Any) -> StoredConversation | None:
    """Parse one serialized legacy JSON conversation record."""

    if not isinstance(item, dict):
        return None
    messages: list[StoredConversationMessage] = []
    for raw_message in item.get("messages", []):
        message = _parse_legacy_message(raw_message)
        if message is not None:
            messages.append(message)
    if not messages:
        return None

    try:
        created_at = datetime.fromisoformat(str(item["created_at"]))
        updated_at = datetime.fromisoformat(str(item.get("updated_at", item["created_at"])))
        conversation_id = str(item["conversation_id"])
    except (KeyError, TypeError, ValueError):
        return None

    active_window = _parse_legacy_window(item.get("active_window"))
    return StoredConversation(
        conversation_id=conversation_id,
        title=_conversation_title_from_messages(messages),
        created_at=created_at,
        updated_at=updated_at,
        active_window=active_window,
        degraded=bool(item.get("degraded", False)),
        summary=None,
        provider_name=next(
            (
                message.provider_name
                for message in reversed(messages)
                if message.role == "assistant" and message.provider_name
            ),
            None,
        ),
        model_name=next(
            (
                message.model_name
                for message in reversed(messages)
                if message.role == "assistant" and message.model_name
            ),
            None,
        ),
        archived=False,
        pinned=False,
        messages=messages,
    )


def _parse_legacy_message(raw_message: Any) -> StoredConversationMessage | None:
    """Parse one serialized legacy JSON conversation message."""

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


def _parse_legacy_window(raw_window: Any) -> WindowInfo | None:
    """Parse one serialized legacy window payload."""

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


def _window_from_columns(row: sqlite3.Row) -> WindowInfo | None:
    """Build a window payload from SQLite row columns."""

    app_name = _optional_string(row["active_window_app"])
    window_title = _optional_string(row["active_window_title"])
    workspace = _optional_string(row["active_window_workspace"])
    if app_name is None and window_title is None and workspace is None:
        return None
    return WindowInfo(
        app_name=app_name or "",
        window_title=window_title or "",
        workspace=workspace or "",
    )


def _conversation_title(conversation: StoredConversation) -> str:
    """Derive a stable thread title from the first user message."""

    return conversation.title or _conversation_title_from_messages(conversation.messages)


def _conversation_title_from_messages(messages: list[StoredConversationMessage]) -> str:
    """Derive a stable thread title from the first user message list."""

    for message in messages:
        if message.role == "user" and message.text.strip():
            title = " ".join(message.text.split())
            return title[:120] if title else "Conversation"
    return "Conversation"


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
