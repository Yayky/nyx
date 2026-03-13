"""Tests for the Phase 12 git/GitHub module."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.config import load_config
from nyx.modules.git_github import GitHubCommandError, GitHubModule
from nyx.providers.base import ProviderQueryResult


@dataclass
class SequentialRegistry:
    """Small provider-registry stub that returns queued results in order."""

    results: list[ProviderQueryResult]
    seen_preferred_provider_names: list[str | None] | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return the next queued provider result."""

        del prompt, context
        if self.seen_preferred_provider_names is None:
            self.seen_preferred_provider_names = []
        self.seen_preferred_provider_names.append(preferred_provider_name)
        return self.results.pop(0)


class FakeGitHubModule(GitHubModule):
    """Git/GitHub module variant backed by deterministic fake subprocess output."""

    def __init__(
        self,
        *,
        command_map: dict[tuple[str, ...], str],
        config,
        provider_registry,
    ) -> None:
        """Initialize the fake module with a command-output mapping."""

        super().__init__(config=config, provider_registry=provider_registry, logger=logging.getLogger("test"))
        self.command_map = command_map
        self.seen_commands: list[tuple[str, ...]] = []

    async def _run_command(self, *command: str, cwd: Path | None = None) -> str:
        """Return deterministic output for one expected command invocation."""

        del cwd
        self.seen_commands.append(tuple(command))
        try:
            return self.command_map[tuple(command)]
        except KeyError as exc:
            raise GitHubCommandError(f"Unexpected command in test: {command}") from exc


@pytest.mark.anyio
async def test_git_module_creates_lists_and_applies_push_proposals(tmp_path: Path) -> None:
    """Push requests should create explicit proposals before running git push."""

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config = load_config(tmp_path / "config.toml")
    registry = SequentialRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"propose_push","arguments":{"remote":"origin","branch":"main"}}',
                fallback_used=False,
            )
        ]
    )
    module = FakeGitHubModule(
        command_map={
            ("git", "rev-parse", "--show-toplevel"): str(repo_root),
            ("git", "branch", "--show-current"): "main",
            ("git", "status", "--short"): "M nyx.py",
            ("git", "remote"): "origin",
            ("git", "remote", "get-url", "origin"): "git@github.com:yayky/AIAssistnat.git",
            ("git", "push", "origin", "main"): "Everything up-to-date",
        },
        config=config,
        provider_registry=registry,
    )

    create_result = await module.handle("push current branch", model_override="codex-cli")
    proposal_id = create_result.response_text.split()[3]

    list_result = await module.handle("list push proposals", model_override=None)
    apply_result = await module.handle(f"apply push proposal {proposal_id}", model_override=None)

    assert create_result.operation == "propose_push"
    assert proposal_id in list_result.response_text
    assert "Everything up-to-date" in apply_result.response_text
    assert ("git", "push", "origin", "main") in module.seen_commands


@pytest.mark.anyio
async def test_git_module_summarizes_diff_via_provider(tmp_path: Path) -> None:
    """Diff summary requests should collect git output and ask the provider to summarize it."""

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config = load_config(tmp_path / "config.toml")
    registry = SequentialRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"summarize_diff","arguments":{}}',
                fallback_used=False,
            ),
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text="Changed nyx/config.py and added tests. Main risk is config merge behavior.",
                fallback_used=False,
            ),
        ]
    )
    module = FakeGitHubModule(
        command_map={
            ("git", "rev-parse", "--show-toplevel"): str(repo_root),
            ("git", "branch", "--show-current"): "main",
            ("git", "status", "--short"): "M nyx/config.py\nA tests/test_config.py",
            ("git", "remote"): "origin",
            ("git", "diff", "--stat"): " nyx/config.py | 10 +++++-----",
            ("git", "diff", "--cached", "--stat"): "",
            ("git", "diff", "--unified=0", "--no-color"): "@@ -1 +1 @@",
            ("git", "diff", "--cached", "--unified=0", "--no-color"): "",
        },
        config=config,
        provider_registry=registry,
    )

    result = await module.handle("summarize the git diff", model_override="codex-cli")

    assert result.operation == "summarize_diff"
    assert "Main risk is config merge behavior" in result.response_text
    assert registry.seen_preferred_provider_names == ["codex-cli", "codex-cli"]


@pytest.mark.anyio
async def test_git_module_lists_github_issues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue-list requests should format ``gh issue list`` JSON into readable lines."""

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config = load_config(tmp_path / "config.toml")
    registry = SequentialRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"list_issues","arguments":{"state":"open","limit":2}}',
                fallback_used=False,
            )
        ]
    )
    monkeypatch.setattr("nyx.modules.git_github.shutil.which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    module = FakeGitHubModule(
        command_map={
            ("git", "rev-parse", "--show-toplevel"): str(repo_root),
            ("git", "branch", "--show-current"): "main",
            ("git", "status", "--short"): "",
            ("git", "remote"): "origin",
            (
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                "2",
                "--json",
                "number,title,state,url",
            ): json.dumps(
                [
                    {
                        "number": 12,
                        "title": "Fix launcher summon behavior",
                        "state": "OPEN",
                        "url": "https://github.com/example/repo/issues/12",
                    }
                ]
            ),
        },
        config=config,
        provider_registry=registry,
    )

    result = await module.handle("list open github issues", model_override="codex-cli")

    assert "Fix launcher summon behavior" in result.response_text
    assert "#12" in result.response_text


@pytest.mark.anyio
async def test_git_module_commit_adds_all_before_commit(tmp_path: Path) -> None:
    """Commit requests should stage changes when the planner asks for include_all."""

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config = load_config(tmp_path / "config.toml")
    registry = SequentialRegistry(
        results=[
            ProviderQueryResult(
                provider_name="codex-cli",
                provider_type="subprocess-cli",
                model_name=None,
                text='{"operation":"commit","arguments":{"message":"Implement Phase 12","include_all":true}}',
                fallback_used=False,
            )
        ]
    )
    module = FakeGitHubModule(
        command_map={
            ("git", "rev-parse", "--show-toplevel"): str(repo_root),
            ("git", "branch", "--show-current"): "main",
            ("git", "status", "--short"): "M nyx/modules/git_github.py",
            ("git", "remote"): "origin",
            ("git", "add", "-A"): "",
            ("git", "status", "--porcelain"): "M nyx/modules/git_github.py",
            ("git", "commit", "-m", "Implement Phase 12"): "[main abc1234] Implement Phase 12",
        },
        config=config,
        provider_registry=registry,
    )

    result = await module.handle("commit the current changes", model_override="codex-cli")

    assert result.operation == "commit"
    assert "Implement Phase 12" in result.response_text
    assert ("git", "add", "-A") in module.seen_commands


@pytest.mark.anyio
async def test_git_module_reports_clear_error_outside_git_repo(tmp_path: Path) -> None:
    """Git/GitHub requests should fail clearly when run outside a repository."""

    config = load_config(tmp_path / "config.toml")
    registry = SequentialRegistry(results=[])
    module = FakeGitHubModule(
        command_map={},
        config=config,
        provider_registry=registry,
    )

    async def failing_run_command(*command: str, cwd: Path | None = None) -> str:
        del command, cwd
        raise GitHubCommandError(
            "Command git rev-parse --show-toplevel failed with code 128: "
            "fatal: not a git repository (or any parent up to mount point /)"
        )

    module._run_command = failing_run_command  # type: ignore[method-assign]

    with pytest.raises(GitHubCommandError, match="inside a git repository"):
        await module.handle("summarize the git diff", model_override="codex-cli")
