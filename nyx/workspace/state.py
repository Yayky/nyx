"""Local JSON-backed state for the Nyx Workspace shell."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path

DEFAULT_WORKSPACE_PROJECTS_PATH = Path("~/.local/state/nyx/workspace_projects.json").expanduser()
DEFAULT_WORKSPACE_STATE_PATH = Path("~/.local/state/nyx/workspace_state.json").expanduser()


@dataclass(slots=True)
class WorkspaceProject:
    """One tracked Git repo shown in the workspace project pane."""

    project_id: str
    display_name: str
    root_path: str
    repo_detected: bool
    last_opened_at: str | None = None
    pinned: bool = False
    preferred_provider: str | None = None
    preferred_mode: str | None = None
    preferred_access: str | None = None
    linked_note_project: str | None = None


@dataclass(slots=True)
class WorkspaceUiState:
    """Persisted UI state for the standalone workspace shell."""

    selected_section: str = "workspace"
    selected_project_id: str | None = None
    selected_thread_id: str | None = None
    provider_name: str | None = None
    mode: str | None = None
    access_mode: str | None = None
    search_text: str = ""
    terminal_visible: bool = True
    diff_visible: bool = False


class WorkspaceProjectRegistry:
    """Load and save the tracked workspace project list."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or DEFAULT_WORKSPACE_PROJECTS_PATH).expanduser()

    def load(self) -> list[WorkspaceProject]:
        """Return all saved workspace projects, newest first when possible."""

        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        projects: list[WorkspaceProject] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                projects.append(WorkspaceProject(**item))
            except TypeError:
                continue
        projects.sort(key=lambda item: (item.pinned, item.last_opened_at or ""), reverse=True)
        return projects

    def save(self, projects: list[WorkspaceProject]) -> None:
        """Persist the tracked workspace project list."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([asdict(project) for project in projects], indent=2) + "\n",
            encoding="utf-8",
        )


class WorkspaceUiStateStore:
    """Load and save shell-only workspace UI state."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or DEFAULT_WORKSPACE_STATE_PATH).expanduser()

    def load(self) -> WorkspaceUiState:
        """Return the saved workspace UI state or defaults."""

        if not self.path.exists():
            return WorkspaceUiState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return WorkspaceUiState()
        if not isinstance(payload, dict):
            return WorkspaceUiState()
        try:
            return WorkspaceUiState(**payload)
        except TypeError:
            return WorkspaceUiState()

    def save(self, state: WorkspaceUiState) -> None:
        """Persist the workspace UI state."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")


def seed_default_project(repo_root: Path, *, provider_name: str | None = None) -> WorkspaceProject:
    """Return one default project entry for a discovered repository root."""

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return WorkspaceProject(
        project_id=repo_root.name.lower().replace(" ", "-") or "project",
        display_name=repo_root.name or str(repo_root),
        root_path=str(repo_root),
        repo_detected=True,
        last_opened_at=now,
        preferred_provider=provider_name,
    )
