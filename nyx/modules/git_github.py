"""Git and GitHub module for Nyx.

Phase 12 adds explicit repository actions for the current working tree using
``git`` and ``gh`` subprocesses. The module supports commit, push with an
explicit confirmation proposal, pull, PR creation, issue listing, and provider-
backed diff summaries while keeping all subprocess calls asynchronous.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

from nyx.config import NyxConfig
from nyx.providers.base import ProviderQueryResult
from nyx.providers.registry import ProviderRegistry

_GIT_PATTERNS = (
    re.compile(r"\bgit\b", re.IGNORECASE),
    re.compile(r"\bgithub\b", re.IGNORECASE),
    re.compile(r"\bcommit\b", re.IGNORECASE),
    re.compile(r"\bpush\b", re.IGNORECASE),
    re.compile(r"\bpull\b", re.IGNORECASE),
    re.compile(r"\bpull request\b", re.IGNORECASE),
    re.compile(r"\bpr\b", re.IGNORECASE),
    re.compile(r"\bissues?\b", re.IGNORECASE),
    re.compile(r"\bdiff\b", re.IGNORECASE),
)
_LIST_PUSH_PROPOSALS_PATTERNS = (
    re.compile(r"\blist\b.+\bpush proposals\b", re.IGNORECASE),
    re.compile(r"\bshow\b.+\bpush proposals\b", re.IGNORECASE),
    re.compile(r"\bpending\b.+\bpush proposals\b", re.IGNORECASE),
)
_APPLY_PUSH_PROPOSAL_PATTERN = re.compile(
    r"\b(?:apply|accept|confirm)\b.+\bpush proposal\b(?:\s+(?P<proposal>[a-f0-9]{8}))?",
    re.IGNORECASE,
)
_SKIP_PUSH_PROPOSAL_PATTERN = re.compile(
    r"\b(?:skip|reject|discard|cancel)\b.+\bpush proposal\b(?:\s+(?P<proposal>[a-f0-9]{8}))?",
    re.IGNORECASE,
)
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_ALLOWED_OPERATIONS = {
    "commit",
    "propose_push",
    "pull",
    "create_pr",
    "list_issues",
    "summarize_diff",
    "reject",
}


class GitHubCommandError(RuntimeError):
    """Raised when a git or gh command fails or the repo is unusable."""


@dataclass(slots=True)
class GitPushProposal:
    """One pending git push confirmation proposal."""

    proposal_id: str
    created_at: str
    repo_root: str
    remote: str
    branch: str
    status: str
    source_request: str


@dataclass(slots=True)
class GitHubPlan:
    """Validated provider-produced git/github action plan."""

    operation: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(slots=True)
class GitHubResult:
    """Structured result returned by the Phase 12 git/github module."""

    response_text: str
    used_model: str
    model_name: str | None
    token_count: int | None
    degraded: bool
    operation: str


class GitHubModule:
    """Handle explicit git and GitHub requests for the current repository."""

    def __init__(
        self,
        config: NyxConfig,
        provider_registry: ProviderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the module with explicit config and provider dependencies."""

        self.config = config
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.modules.git_github")

    @classmethod
    def matches_request(cls, text: str) -> bool:
        """Return whether the prompt is an obvious git or GitHub request."""

        normalized = text.strip()
        if not normalized:
            return False
        return any(
            pattern.search(normalized)
            for pattern in (
                *_GIT_PATTERNS,
                *_LIST_PUSH_PROPOSALS_PATTERNS,
                _APPLY_PUSH_PROPOSAL_PATTERN,
                _SKIP_PUSH_PROPOSAL_PATTERN,
            )
        )

    async def handle(self, request_text: str, model_override: str | None = None) -> GitHubResult:
        """Handle one git/GitHub request for the current repository."""

        await self._ensure_layout()

        direct_result = await self._handle_direct_command(request_text)
        if direct_result is not None:
            return direct_result

        repo_root = await self._repo_root()
        repo_context = await self._repo_context(repo_root)
        provider_result = await self.provider_registry.query(
            prompt=self._build_planner_prompt(request_text, repo_context),
            context=self._planner_context(repo_context),
            preferred_provider_name=model_override,
        )
        plan = self._parse_plan(provider_result.text)
        self.logger.info(
            "Git/GitHub planner selected operation=%s provider=%s",
            plan.operation,
            provider_result.provider_name,
        )

        if plan.operation == "reject":
            reason = self._require_string_argument(plan.arguments, "reason")
            return self._result_from_provider(provider_result, reason, plan.operation)

        if plan.operation == "summarize_diff":
            return await self._summarize_diff(
                provider_name=model_override,
                repo_root=repo_root,
                planner_result=provider_result,
            )

        if plan.operation == "commit":
            response_text = await self._commit(repo_root, plan.arguments)
        elif plan.operation == "propose_push":
            response_text = await self._propose_push(repo_root, request_text, plan.arguments)
        elif plan.operation == "pull":
            response_text = await self._pull(repo_root, plan.arguments)
        elif plan.operation == "create_pr":
            response_text = await self._create_pr(repo_root, plan.arguments)
        elif plan.operation == "list_issues":
            response_text = await self._list_issues(repo_root, plan.arguments)
        else:
            raise ValueError(f"Unsupported git/github operation: {plan.operation!r}")

        return self._result_from_provider(provider_result, response_text, plan.operation)

    async def _handle_direct_command(self, request_text: str) -> GitHubResult | None:
        """Handle direct push-proposal management commands."""

        if any(pattern.search(request_text) for pattern in _LIST_PUSH_PROPOSALS_PATTERNS):
            return await self._list_pending_push_proposals()

        apply_match = _APPLY_PUSH_PROPOSAL_PATTERN.search(request_text)
        if apply_match is not None:
            return await self._apply_push_proposal(apply_match.group("proposal"))

        skip_match = _SKIP_PUSH_PROPOSAL_PATTERN.search(request_text)
        if skip_match is not None:
            return await self._skip_push_proposal(skip_match.group("proposal"))

        return None

    async def _ensure_layout(self) -> None:
        """Ensure the push-proposal store exists."""

        def _sync_ensure() -> None:
            self.config.config_path.parent.mkdir(parents=True, exist_ok=True)
            proposals = self._push_proposals_path()
            if not proposals.exists():
                proposals.write_text("[]\n", encoding="utf-8")

        await asyncio.to_thread(_sync_ensure)

    async def _repo_root(self) -> Path:
        """Return the current repository root or raise a descriptive error."""

        try:
            stdout = await self._run_command("git", "rev-parse", "--show-toplevel")
        except GitHubCommandError as exc:
            message = str(exc)
            if "not a git repository" in message:
                raise GitHubCommandError(
                    "Nyx can only run git/github actions from inside a git repository. "
                    "Change into the repo directory and try again."
                ) from exc
            raise
        repo_root = Path(stdout.strip())
        if not repo_root.exists():
            raise GitHubCommandError(f"Resolved repository root does not exist: {repo_root}")
        return repo_root

    async def _repo_context(self, repo_root: Path) -> dict[str, Any]:
        """Return lightweight repository context for planning."""

        branch = await self._current_branch(repo_root)
        status = await self._run_command("git", "status", "--short", cwd=repo_root)
        remote = await self._default_remote(repo_root)
        return {
            "repo_root": str(repo_root),
            "branch": branch,
            "remote": remote,
            "dirty": bool(status.strip()),
            "status_preview": self._limited_lines(status, limit=20),
            "gh_available": shutil.which("gh") is not None and self.config.git.gh_cli,
            "push_confirmation_required": True,
            "use_ssh": self.config.git.use_ssh,
        }

    def _planner_context(self, repo_context: dict[str, Any]) -> dict[str, Any]:
        """Return provider planning context for git/github requests."""

        return {
            "module": "git_github",
            "allowed_operations": sorted(_ALLOWED_OPERATIONS - {"reject"}),
            **repo_context,
        }

    def _build_planner_prompt(self, request_text: str, repo_context: dict[str, Any]) -> str:
        """Build the provider prompt for git/github action selection."""

        return (
            "You are Nyx's Phase 12 git/github planner. "
            "Return exactly one JSON object with keys operation, arguments, and rationale. "
            "Do not return markdown or extra prose. Allowed operations: "
            "commit, propose_push, pull, create_pr, list_issues, summarize_diff, reject. "
            "Push must always use propose_push because user confirmation is required before executing git push. "
            "Use the current repository only.\n\n"
            "Argument rules:\n"
            '- commit: {"message": str, "include_all": bool}\n'
            '- propose_push: {"remote": str|null, "branch": str|null}\n'
            '- pull: {"remote": str|null, "branch": str|null}\n'
            '- create_pr: {"title": str, "body": str, "base": str|null, "head": str|null}\n'
            '- list_issues: {"state": "open"|"closed"|"all", "limit": int}\n'
            '- summarize_diff: {}\n'
            '- reject: {"reason": str}\n\n'
            f"Repository context: {json.dumps(repo_context, sort_keys=True, default=str)}\n\n"
            f"User request: {request_text}"
        )

    def _parse_plan(self, planner_text: str) -> GitHubPlan:
        """Parse and validate one planner JSON object."""

        payload = self._extract_json_object(planner_text)
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Git/GitHub planner must return a JSON object.")

        operation = decoded.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported git/github operation: {operation!r}")

        arguments = decoded.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Git/GitHub planner 'arguments' must be a JSON object.")

        rationale = decoded.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Git/GitHub planner 'rationale' must be a string when present.")

        return GitHubPlan(operation=operation, arguments=arguments, rationale=rationale)

    def _extract_json_object(self, text: str) -> str:
        """Extract a JSON object from raw provider output."""

        fenced_match = _JSON_BLOCK_PATTERN.search(text)
        if fenced_match is not None:
            return fenced_match.group(1).strip()

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        object_match = _JSON_OBJECT_PATTERN.search(text)
        if object_match is None:
            raise ValueError("Git/GitHub planner did not return a JSON object.")
        return object_match.group(0).strip()

    async def _commit(self, repo_root: Path, arguments: dict[str, Any]) -> str:
        """Commit repository changes with a planner-produced message."""

        message = self._require_string_argument(arguments, "message")
        include_all = bool(arguments.get("include_all", True))
        if include_all:
            await self._run_command("git", "add", "-A", cwd=repo_root)

        status = await self._run_command("git", "status", "--porcelain", cwd=repo_root)
        if not status.strip():
            return "No changes are available to commit."

        output = await self._run_command("git", "commit", "-m", message, cwd=repo_root)
        return output.strip() or f"Committed changes with message: {message}"

    async def _propose_push(self, repo_root: Path, source_request: str, arguments: dict[str, Any]) -> str:
        """Create a persisted push proposal instead of executing git push immediately."""

        remote = self._optional_string_argument(arguments, "remote") or await self._default_remote(repo_root)
        branch = self._optional_string_argument(arguments, "branch") or await self._current_branch(repo_root)
        proposal = await self._create_push_proposal(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            source_request=source_request,
        )
        return (
            f"Created push proposal {proposal.proposal_id} for {proposal.remote}/{proposal.branch}.\n"
            f"Apply with: apply push proposal {proposal.proposal_id}\n"
            f"Skip with: skip push proposal {proposal.proposal_id}"
        )

    async def _pull(self, repo_root: Path, arguments: dict[str, Any]) -> str:
        """Pull the current repository using a fast-forward-only strategy."""

        remote = self._optional_string_argument(arguments, "remote")
        branch = self._optional_string_argument(arguments, "branch")
        await self._ensure_ssh_remote(repo_root, remote)

        command = ["git", "pull", "--ff-only"]
        if remote and branch:
            command.extend([remote, branch])
        elif remote:
            command.append(remote)

        output = await self._run_command(*command, cwd=repo_root)
        return output.strip() or "Pulled latest changes with fast-forward only."

    async def _create_pr(self, repo_root: Path, arguments: dict[str, Any]) -> str:
        """Create a GitHub pull request using the ``gh`` CLI."""

        self._require_gh_available()
        title = self._require_string_argument(arguments, "title")
        body = self._require_string_argument(arguments, "body")
        base = self._optional_string_argument(arguments, "base")
        head = self._optional_string_argument(arguments, "head")

        command = ["gh", "pr", "create", "--title", title, "--body", body]
        if base:
            command.extend(["--base", base])
        if head:
            command.extend(["--head", head])

        output = await self._run_command(*command, cwd=repo_root)
        return output.strip() or "Created pull request."

    async def _list_issues(self, repo_root: Path, arguments: dict[str, Any]) -> str:
        """List GitHub issues in the current repository."""

        self._require_gh_available()
        state = self._optional_string_argument(arguments, "state") or "open"
        if state not in {"open", "closed", "all"}:
            raise ValueError(f"Unsupported GitHub issue state: {state!r}")

        limit = int(arguments.get("limit", 10))
        command = [
            "gh",
            "issue",
            "list",
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            "number,title,state,url",
        ]
        output = await self._run_command(*command, cwd=repo_root)
        issues = json.loads(output) if output.strip() else []
        if not isinstance(issues, list):
            raise ValueError("GitHub issue list response was not a JSON array.")
        if not issues:
            return f"No {state} issues found."

        lines = [f"GitHub issues ({state}):"]
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            lines.append(
                f"- #{issue.get('number')} [{issue.get('state')}] {issue.get('title')} — {issue.get('url')}"
            )
        return "\n".join(lines)

    async def _summarize_diff(
        self,
        provider_name: str | None,
        repo_root: Path,
        planner_result: ProviderQueryResult,
    ) -> GitHubResult:
        """Summarize the current diff using the provider layer."""

        status = await self._run_command("git", "status", "--short", cwd=repo_root)
        if not status.strip():
            return self._result_from_provider(
                planner_result,
                "Working tree is clean. There is no diff to summarize.",
                "summarize_diff",
            )

        diff_stat = await self._run_command("git", "diff", "--stat", cwd=repo_root)
        cached_stat = await self._run_command("git", "diff", "--cached", "--stat", cwd=repo_root)
        diff_text = await self._run_command("git", "diff", "--unified=0", "--no-color", cwd=repo_root)
        cached_diff_text = await self._run_command(
            "git",
            "diff",
            "--cached",
            "--unified=0",
            "--no-color",
            cwd=repo_root,
        )
        summary_prompt = (
            "You are Nyx's Phase 12 git diff summarizer. "
            "Summarize the current repository changes concisely. "
            "Mention the main files affected, what changed at a high level, and any obvious risks. "
            "Do not invent details that are not in the diff.\n\n"
            f"git status --short:\n{self._limited_text(status, 4000)}\n\n"
            f"git diff --stat:\n{self._limited_text(diff_stat, 3000)}\n\n"
            f"git diff --cached --stat:\n{self._limited_text(cached_stat, 3000)}\n\n"
            f"git diff --unified=0:\n{self._limited_text(diff_text, 12000)}\n\n"
            f"git diff --cached --unified=0:\n{self._limited_text(cached_diff_text, 12000)}"
        )
        provider_result = await self.provider_registry.query(
            prompt=summary_prompt,
            context={
                "module": "git_github",
                "repo_root": str(repo_root),
                "operation": "summarize_diff",
            },
            preferred_provider_name=provider_name,
        )
        return GitHubResult(
            response_text=provider_result.text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used,
            operation="summarize_diff",
        )

    async def _list_pending_push_proposals(self) -> GitHubResult:
        """Return all pending push proposals from the persisted store."""

        proposals = [proposal for proposal in await self._load_push_proposals() if proposal.status == "pending"]
        if not proposals:
            return self._direct_result("No pending push proposals.", "list_push_proposals")

        lines = ["Pending push proposals:"]
        for proposal in proposals:
            lines.append(
                f"- {proposal.proposal_id} [{proposal.remote}/{proposal.branch}] {proposal.repo_root}"
            )
        return self._direct_result("\n".join(lines), "list_push_proposals")

    async def _apply_push_proposal(self, proposal_id: str | None) -> GitHubResult:
        """Execute one pending push proposal after explicit user confirmation."""

        proposal = await self._resolve_push_proposal(proposal_id)
        repo_root = Path(proposal.repo_root)
        await self._ensure_ssh_remote(repo_root, proposal.remote)
        output = await self._run_command("git", "push", proposal.remote, proposal.branch, cwd=repo_root)
        proposal.status = "applied"
        await self._save_push_proposal(proposal)
        response = output.strip() or f"Pushed {proposal.branch} to {proposal.remote}."
        return self._direct_result(response, "apply_push_proposal")

    async def _skip_push_proposal(self, proposal_id: str | None) -> GitHubResult:
        """Mark one pending push proposal as skipped."""

        proposal = await self._resolve_push_proposal(proposal_id)
        proposal.status = "skipped"
        await self._save_push_proposal(proposal)
        return self._direct_result(f"Skipped push proposal {proposal.proposal_id}.", "skip_push_proposal")

    async def _create_push_proposal(
        self,
        *,
        repo_root: Path,
        remote: str,
        branch: str,
        source_request: str,
    ) -> GitPushProposal:
        """Create and persist a new push proposal."""

        proposal = GitPushProposal(
            proposal_id=uuid.uuid4().hex[:8],
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            repo_root=str(repo_root),
            remote=remote,
            branch=branch,
            status="pending",
            source_request=source_request,
        )
        proposals = await self._load_push_proposals()
        proposals.append(proposal)
        await self._write_push_proposals(proposals)
        return proposal

    async def _resolve_push_proposal(self, proposal_id: str | None) -> GitPushProposal:
        """Resolve one pending push proposal by id or most-recent fallback."""

        proposals = [proposal for proposal in await self._load_push_proposals() if proposal.status == "pending"]
        if not proposals:
            raise GitHubCommandError("There are no pending push proposals.")

        if proposal_id is not None:
            for proposal in proposals:
                if proposal.proposal_id == proposal_id:
                    return proposal
            raise GitHubCommandError(f"Could not find pending push proposal {proposal_id}.")

        return proposals[-1]

    async def _load_push_proposals(self) -> list[GitPushProposal]:
        """Load push proposals from disk."""

        def _sync_load() -> list[GitPushProposal]:
            raw = self._push_proposals_path().read_text(encoding="utf-8")
            payload = json.loads(raw) if raw.strip() else []
            if not isinstance(payload, list):
                raise ValueError("Push proposal store must contain a JSON array.")
            return [GitPushProposal(**item) for item in payload]

        return await asyncio.to_thread(_sync_load)

    async def _write_push_proposals(self, proposals: list[GitPushProposal]) -> None:
        """Write the full push proposal list to disk."""

        def _sync_write() -> None:
            payload = [asdict(proposal) for proposal in proposals]
            self._push_proposals_path().write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        await asyncio.to_thread(_sync_write)

    async def _save_push_proposal(self, updated: GitPushProposal) -> None:
        """Persist one updated push proposal back into the store."""

        proposals = await self._load_push_proposals()
        for index, proposal in enumerate(proposals):
            if proposal.proposal_id == updated.proposal_id:
                proposals[index] = updated
                break
        await self._write_push_proposals(proposals)

    async def _current_branch(self, repo_root: Path) -> str:
        """Return the current branch name for the repository."""

        branch = await self._run_command("git", "branch", "--show-current", cwd=repo_root)
        if not branch.strip():
            raise GitHubCommandError("Could not determine the current git branch.")
        return branch.strip()

    async def _default_remote(self, repo_root: Path) -> str:
        """Return the configured default git remote name."""

        remotes = await self._run_command("git", "remote", cwd=repo_root)
        for remote in remotes.splitlines():
            name = remote.strip()
            if name:
                return name
        return "origin"

    async def _ensure_ssh_remote(self, repo_root: Path, remote: str | None) -> None:
        """Validate that the selected remote uses SSH when the config requires it."""

        if not self.config.git.use_ssh:
            return
        remote_name = remote or await self._default_remote(repo_root)
        url = await self._run_command("git", "remote", "get-url", remote_name, cwd=repo_root)
        normalized = url.strip()
        if normalized.startswith("git@") or normalized.startswith("ssh://"):
            return
        raise GitHubCommandError(
            f"Remote '{remote_name}' does not use SSH, but git.use_ssh is enabled."
        )

    def _require_gh_available(self) -> None:
        """Ensure the ``gh`` CLI is enabled and installed."""

        if not self.config.git.gh_cli:
            raise GitHubCommandError("GitHub CLI support is disabled in config.")
        if shutil.which("gh") is None:
            raise GitHubCommandError("The GitHub CLI ('gh') is not installed.")

    async def _run_command(self, *command: str, cwd: Path | None = None) -> str:
        """Run one subprocess command asynchronously and return stdout."""

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd is not None else None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_data, stderr_data = await process.communicate()
        stdout_text = stdout_data.decode().strip()
        stderr_text = stderr_data.decode().strip()
        if process.returncode != 0:
            message = stderr_text or stdout_text or "no error output"
            raise GitHubCommandError(
                f"Command {' '.join(command)} failed with code {process.returncode}: {message}"
            )
        return stdout_text

    def _push_proposals_path(self) -> Path:
        """Return the persisted push proposal store path."""

        return self.config.config_path.parent / "git_push_proposals.json"

    def _require_string_argument(self, arguments: dict[str, Any], key: str) -> str:
        """Return one required string argument from a planner payload."""

        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Git/GitHub operation requires a non-empty string '{key}'.")
        return value.strip()

    def _optional_string_argument(self, arguments: dict[str, Any], key: str) -> str | None:
        """Return one optional string argument when present and non-empty."""

        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Git/GitHub operation argument '{key}' must be a string when present.")
        normalized = value.strip()
        return normalized or None

    def _limited_lines(self, text: str, limit: int) -> list[str]:
        """Return the first ``limit`` non-empty lines from a text block."""

        lines = [line for line in text.splitlines() if line.strip()]
        return lines[:limit]

    def _limited_text(self, text: str, limit: int) -> str:
        """Return text truncated to a maximum character length."""

        stripped = text.strip()
        if len(stripped) <= limit:
            return stripped
        return stripped[: limit - 3] + "..."

    def _result_from_provider(
        self,
        provider_result: ProviderQueryResult,
        response_text: str,
        operation: str,
    ) -> GitHubResult:
        """Build a module result from one provider-registry result."""

        return GitHubResult(
            response_text=response_text,
            used_model=provider_result.provider_name,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
            degraded=provider_result.fallback_used,
            operation=operation,
        )

    def _direct_result(self, response_text: str, operation: str) -> GitHubResult:
        """Build a direct command result that did not query a model provider."""

        return GitHubResult(
            response_text=response_text,
            used_model="direct",
            model_name=None,
            token_count=None,
            degraded=False,
            operation=operation,
        )
