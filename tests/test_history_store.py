"""Tests for SQLite-backed overlay history persistence."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from nyx.ui.history_store import OverlayHistorySnapshot, OverlayHistoryStore, StoredConversation, StoredConversationMessage


def test_overlay_history_store_migrates_legacy_json(tmp_path: Path) -> None:
    """Legacy JSON history should migrate into SQLite on first load."""

    legacy_path = tmp_path / "history.json"
    legacy_payload = {
        "prompt_history": ["hello"],
        "conversations": [
            {
                "conversation_id": "legacy-thread",
                "created_at": "2026-03-15T12:00:00+00:00",
                "updated_at": "2026-03-15T12:00:00+00:00",
                "degraded": False,
                "active_window": {
                    "app_name": "kitty",
                    "window_title": "legacy",
                    "workspace": "1",
                },
                "messages": [
                    {
                        "role": "user",
                        "text": "hello",
                        "created_at": "2026-03-15T12:00:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "text": "hi",
                        "created_at": "2026-03-15T12:00:01+00:00",
                        "provider_name": "codex-cli",
                    },
                ],
            }
        ],
    }
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    store = OverlayHistoryStore(legacy_path)
    snapshot = store.load()

    assert snapshot.prompt_history == ["hello"]
    assert len(snapshot.conversations) == 1
    assert snapshot.conversations[0].conversation_id == "legacy-thread"
    assert legacy_path.with_suffix(".json.migrated.bak").exists()
    assert store.path.exists()


def test_overlay_history_store_preserves_all_saved_items(tmp_path: Path) -> None:
    """Saving history should not silently truncate prompts or conversations."""

    store = OverlayHistoryStore(tmp_path / "history.db")
    now = datetime.now().astimezone()
    snapshot = OverlayHistorySnapshot(
        prompt_history=[f"prompt {index}" for index in range(205)],
        conversations=[
            StoredConversation(
                conversation_id=f"thread-{index}",
                title=f"Thread {index}",
                created_at=now,
                updated_at=now,
                active_window=None,
                degraded=False,
                summary=None,
                provider_name="codex-cli",
                model_name=None,
                archived=False,
                pinned=False,
                messages=[
                    StoredConversationMessage(
                        role="user",
                        text=f"prompt {index}",
                        created_at=now,
                    ),
                    StoredConversationMessage(
                        role="assistant",
                        text=f"reply {index}",
                        created_at=now,
                        provider_name="codex-cli",
                    ),
                ],
            )
            for index in range(205)
        ],
    )

    store.save(snapshot)
    restored = store.load()

    assert len(restored.prompt_history) == 205
    assert len(restored.conversations) == 205
    restored_by_id = {conversation.conversation_id: conversation for conversation in restored.conversations}
    assert restored_by_id["thread-0"].title == "Thread 0"
