"""Git repository discovery helpers for the Nyx workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from nyx.workspace.models import WorkspaceRepoStatus


class WorkspaceRepoError(RuntimeError):
    """Raised when one requested workspace project path is not a usable Git repo."""


@dataclass(slots=True)
class WorkspaceRepoService:
    """Small synchronous helper for validating and inspecting local Git repos."""

    git_binary: str = "git"

    def discover(self, requested_path: Path) -> WorkspaceRepoStatus:
        """Resolve one path to its repository root and current branch state."""

        candidate = requested_path.expanduser()
        if not candidate.exists():
            raise WorkspaceRepoError(f"Path does not exist: {candidate}")
        working_dir = candidate if candidate.is_dir() else candidate.parent
        repo_root = self._run_git(working_dir, "rev-parse", "--show-toplevel")
        branch_name = self._run_git(Path(repo_root), "branch", "--show-current").strip() or None
        status_text = self._run_git(Path(repo_root), "status", "--short")
        head_summary = self._run_git(Path(repo_root), "log", "-1", "--pretty=%h %s").strip() or None
        return WorkspaceRepoStatus(
            repo_root=str(Path(repo_root).expanduser().resolve()),
            branch_name=branch_name,
            dirty=bool(status_text.strip()),
            head_summary=head_summary,
        )

    def _run_git(self, cwd: Path, *args: str) -> str:
        """Run one non-interactive Git command or raise a descriptive error."""

        result = subprocess.run(
            [self.git_binary, *args],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        stderr = (result.stderr or result.stdout).strip()
        if "not a git repository" in stderr.lower():
            raise WorkspaceRepoError(
                f"{cwd} is not inside a Git repository. Add a repo root or any path inside a repo."
            )
        raise WorkspaceRepoError(stderr or f"Git command failed in {cwd}: {' '.join(args)}")
