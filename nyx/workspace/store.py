"""SQLite persistence for Nyx workspace projects and threads."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from nyx.workspace.models import (
    DEFAULT_WORKSPACE_DB_PATH,
    WorkspaceProjectRecord,
    WorkspaceThreadRecord,
)


class WorkspaceStore:
    """Persist workspace projects, threads, and future run artifacts in SQLite."""

    def __init__(self, path: Path | None = None, *, logger: logging.Logger | None = None) -> None:
        """Store the workspace DB path used for later CRUD operations."""

        self.path = (path or DEFAULT_WORKSPACE_DB_PATH).expanduser()
        self.logger = logger or logging.getLogger("nyx.workspace.store")
        self._initialized = False

    def list_projects(self, search: str | None = None) -> list[WorkspaceProjectRecord]:
        """Return tracked projects ordered by most recently updated."""

        self._ensure_initialized()
        query = """
            SELECT
                id,
                name,
                repo_path,
                linked_note_project,
                default_provider,
                default_mode,
                default_access,
                created_at,
                updated_at
            FROM projects
        """
        params: list[Any] = []
        normalized = (search or "").strip()
        if normalized:
            query += " WHERE name LIKE ? OR repo_path LIKE ?"
            like_value = f"%{normalized}%"
            params.extend([like_value, like_value])
        query += " ORDER BY updated_at DESC, name COLLATE NOCASE ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_project_from_row(row) for row in rows]

    def get_project(self, project_id: str) -> WorkspaceProjectRecord | None:
        """Return one tracked project by id."""

        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    repo_path,
                    linked_note_project,
                    default_provider,
                    default_mode,
                    default_access,
                    created_at,
                    updated_at
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return _project_from_row(row)

    def get_project_by_repo_path(self, repo_path: str) -> WorkspaceProjectRecord | None:
        """Return one tracked project by canonical repository path."""

        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    repo_path,
                    linked_note_project,
                    default_provider,
                    default_mode,
                    default_access,
                    created_at,
                    updated_at
                FROM projects
                WHERE repo_path = ?
                """,
                (repo_path,),
            ).fetchone()
        if row is None:
            return None
        return _project_from_row(row)

    def create_project(
        self,
        *,
        name: str,
        repo_path: str,
        linked_note_project: str | None,
        default_provider: str | None,
        default_mode: str,
        default_access: str,
    ) -> WorkspaceProjectRecord:
        """Create and persist one new tracked project."""

        self._ensure_initialized()
        now = _now()
        project_id = uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    id,
                    name,
                    repo_path,
                    linked_note_project,
                    default_provider,
                    default_mode,
                    default_access,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name,
                    repo_path,
                    linked_note_project,
                    default_provider,
                    default_mode,
                    default_access,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get_project(project_id)  # pragma: no cover - covered through callers

    def touch_project(self, project_id: str) -> None:
        """Update one project's ``updated_at`` timestamp."""

        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (_now(), project_id),
            )
            connection.commit()

    def delete_project(self, project_id: str) -> None:
        """Delete one tracked project and its dependent workspace data."""

        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            connection.commit()

    def list_threads(
        self,
        project_id: str,
        *,
        search: str | None = None,
        include_archived: bool = True,
    ) -> list[WorkspaceThreadRecord]:
        """Return threads for one project ordered by most recently updated."""

        self._ensure_initialized()
        query = """
            SELECT
                id,
                project_id,
                title,
                created_at,
                updated_at,
                provider_name,
                mode,
                access_mode,
                status,
                branch_name,
                worktree_path,
                archived,
                pr_url,
                summary
            FROM threads
            WHERE project_id = ?
        """
        params: list[Any] = [project_id]
        if not include_archived:
            query += " AND archived = 0"
        normalized = (search or "").strip()
        if normalized:
            query += " AND (title LIKE ? OR COALESCE(summary, '') LIKE ?)"
            like_value = f"%{normalized}%"
            params.extend([like_value, like_value])
        query += " ORDER BY archived ASC, updated_at DESC, title COLLATE NOCASE ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_thread_from_row(row) for row in rows]

    def get_thread(self, thread_id: str) -> WorkspaceThreadRecord | None:
        """Return one persisted thread by id."""

        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    project_id,
                    title,
                    created_at,
                    updated_at,
                    provider_name,
                    mode,
                    access_mode,
                    status,
                    branch_name,
                    worktree_path,
                    archived,
                    pr_url,
                    summary
                FROM threads
                WHERE id = ?
                """,
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return _thread_from_row(row)

    def create_thread(
        self,
        *,
        project_id: str,
        title: str,
        provider_name: str | None,
        mode: str,
        access_mode: str,
        status: str = "idle",
        branch_name: str | None = None,
        worktree_path: str | None = None,
        pr_url: str | None = None,
        summary: str | None = None,
    ) -> WorkspaceThreadRecord:
        """Create and persist one new workspace thread."""

        self._ensure_initialized()
        thread_id = uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO threads(
                    id,
                    project_id,
                    title,
                    created_at,
                    updated_at,
                    provider_name,
                    mode,
                    access_mode,
                    status,
                    branch_name,
                    worktree_path,
                    archived,
                    pr_url,
                    summary
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    project_id,
                    title,
                    now,
                    now,
                    provider_name,
                    mode,
                    access_mode,
                    status,
                    branch_name,
                    worktree_path,
                    0,
                    pr_url,
                    summary,
                ),
            )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
            connection.commit()
        return self.get_thread(thread_id)  # pragma: no cover - covered through callers

    def rename_thread(self, thread_id: str, title: str) -> WorkspaceThreadRecord | None:
        """Rename one workspace thread and return the updated record."""

        self._ensure_initialized()
        now = _now()
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, thread_id),
            )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, row["project_id"]))
            connection.commit()
        return self.get_thread(thread_id)

    def set_thread_archived(self, thread_id: str, archived: bool) -> WorkspaceThreadRecord | None:
        """Archive or unarchive one workspace thread."""

        self._ensure_initialized()
        now = _now()
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE threads SET archived = ?, updated_at = ? WHERE id = ?",
                (int(archived), now, thread_id),
            )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, row["project_id"]))
            connection.commit()
        return self.get_thread(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        """Delete one workspace thread."""

        self._ensure_initialized()
        now = _now()
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
            connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            if row is not None:
                connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, row["project_id"]))
            connection.commit()

    def _ensure_initialized(self) -> None:
        """Create the workspace DB schema once."""

        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        self._initialized = True

    def _initialize_schema(self) -> None:
        """Create the workspace schema used by current and future slices."""

        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    repo_path TEXT NOT NULL UNIQUE,
                    linked_note_project TEXT,
                    default_provider TEXT,
                    default_mode TEXT NOT NULL,
                    default_access TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    provider_name TEXT,
                    mode TEXT NOT NULL,
                    access_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    branch_name TEXT,
                    worktree_path TEXT,
                    archived INTEGER NOT NULL DEFAULT 0,
                    pr_url TEXT,
                    summary TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    provider_name TEXT,
                    model_name TEXT,
                    token_count INTEGER,
                    tool_event_json TEXT,
                    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    access_mode TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    branch_name TEXT,
                    worktree_path TEXT,
                    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    path TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_threads_project_updated
                    ON threads(project_id, updated_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """Open one SQLite connection with row access enabled."""

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _now() -> str:
    """Return the current timezone-aware timestamp string."""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _project_from_row(row: sqlite3.Row) -> WorkspaceProjectRecord:
    """Hydrate one project record from a SQLite row."""

    return WorkspaceProjectRecord(
        project_id=str(row["id"]),
        name=str(row["name"]),
        repo_path=str(row["repo_path"]),
        linked_note_project=_optional_string(row["linked_note_project"]),
        default_provider=_optional_string(row["default_provider"]),
        default_mode=str(row["default_mode"]),
        default_access=str(row["default_access"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _thread_from_row(row: sqlite3.Row) -> WorkspaceThreadRecord:
    """Hydrate one thread record from a SQLite row."""

    return WorkspaceThreadRecord(
        thread_id=str(row["id"]),
        project_id=str(row["project_id"]),
        title=str(row["title"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        provider_name=_optional_string(row["provider_name"]),
        mode=str(row["mode"]),
        access_mode=str(row["access_mode"]),
        status=str(row["status"]),
        branch_name=_optional_string(row["branch_name"]),
        worktree_path=_optional_string(row["worktree_path"]),
        archived=bool(int(row["archived"])),
        pr_url=_optional_string(row["pr_url"]),
        summary=_optional_string(row["summary"]),
    )


def _optional_string(value: Any) -> str | None:
    """Normalize nullable string-like SQLite values."""

    if value is None:
        return None
    text = str(value)
    return text if text else None
