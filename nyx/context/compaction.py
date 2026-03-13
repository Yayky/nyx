"""README frontmatter parsing and project relevance ranking for Nyx.

Phase 9 adds the lightweight context-compaction layer described in the
architecture document: load README frontmatter summaries cheaply, rank projects
for a query, then hand only the top candidates to the heavier RAG search path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from pathlib import Path
import re

from nyx.config import NyxConfig

_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)


@dataclass(slots=True)
class ProjectSummary:
    """Cheap project summary loaded from README frontmatter.

    Attributes:
        project_name: Canonical project directory name.
        readme_path: Path to the source README file.
        summary: Human-written or AI-maintained summary string.
        last_updated: Parsed ISO date when present.
        tags: Normalized project tags from frontmatter.
    """

    project_name: str
    readme_path: Path
    summary: str
    last_updated: date | None
    tags: list[str]


@dataclass(slots=True)
class RankedProject:
    """One relevance-ranked project produced by the context compactor."""

    summary: ProjectSummary
    score: float
    matched_terms: list[str]


class ContextCompactor:
    """Load README frontmatter and rank project summaries for a query."""

    def __init__(self, config: NyxConfig, logger: logging.Logger | None = None) -> None:
        """Initialize the compactor from Nyx configuration."""

        self.config = config
        self.logger = logger or logging.getLogger("nyx.context.compaction")

    async def list_project_summaries(self) -> list[ProjectSummary]:
        """Load all project README frontmatter summaries from the notes tree."""

        summaries: list[ProjectSummary] = []
        projects_dir = self.config.notes.projects_dir
        if not projects_dir.exists():
            return summaries

        for child in sorted(projects_dir.iterdir()):
            if not child.is_dir():
                continue
            summary = self._load_project_summary(child)
            if summary is not None:
                summaries.append(summary)
        return summaries

    async def rank_projects(self, query: str, limit: int = 3) -> list[RankedProject]:
        """Return the most relevant projects for the supplied query."""

        summaries = await self.list_project_summaries()
        query_tokens = self._tokenize(query)
        ranked: list[RankedProject] = []
        for summary in summaries:
            score, matched_terms = self._score_summary(summary, query_tokens)
            ranked.append(RankedProject(summary=summary, score=score, matched_terms=matched_terms))

        ranked.sort(
            key=lambda item: (
                item.score,
                item.summary.last_updated or date.min,
                item.summary.project_name.casefold(),
            ),
            reverse=True,
        )

        if limit <= 0:
            return []
        return ranked[: min(limit, len(ranked))]

    def _load_project_summary(self, project_path: Path) -> ProjectSummary | None:
        """Load one project's README frontmatter when present."""

        readme_path = project_path / "README.md"
        if not readme_path.exists():
            return None

        raw_text = readme_path.read_text(encoding="utf-8")
        frontmatter = self._extract_frontmatter(raw_text)
        summary_text = str(frontmatter.get("summary", "")).strip()
        tags = self._parse_tags(frontmatter.get("tags"))
        last_updated = self._parse_date(frontmatter.get("last_updated"))
        return ProjectSummary(
            project_name=project_path.name,
            readme_path=readme_path,
            summary=summary_text,
            last_updated=last_updated,
            tags=tags,
        )

    def _extract_frontmatter(self, text: str) -> dict[str, object]:
        """Extract a limited YAML-frontmatter mapping from README text.

        This parser intentionally supports only the documented scalar/list fields
        used in project summaries: ``summary``, ``last_updated``, and ``tags``.
        """

        stripped = text.lstrip()
        if not stripped.startswith("---\n"):
            return {}

        lines = stripped.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}

        mapping: dict[str, object] = {}
        end_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_index = index
                break
            if ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if key not in {"summary", "last_updated", "tags"}:
                continue
            mapping[key] = self._parse_value(value)
        if end_index is None:
            return {}
        return mapping

    def _parse_value(self, value: str) -> object:
        """Parse one supported frontmatter scalar or inline-list value."""

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [self._strip_quotes(item.strip()) for item in inner.split(",") if item.strip()]
        return self._strip_quotes(value)

    def _strip_quotes(self, value: str) -> str:
        """Remove one layer of matching single or double quotes."""

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value

    def _parse_tags(self, value: object) -> list[str]:
        """Normalize the ``tags`` frontmatter field into a string list."""

        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result

    def _parse_date(self, value: object) -> date | None:
        """Parse the documented ISO ``last_updated`` date field."""

        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            self.logger.debug("Ignoring invalid last_updated value '%s'.", value)
            return None

    def _score_summary(self, summary: ProjectSummary, query_tokens: set[str]) -> tuple[float, list[str]]:
        """Score one project summary for a query using cheap lexical signals."""

        project_tokens = self._tokenize(summary.project_name)
        summary_tokens = self._tokenize(summary.summary)
        tag_tokens = {token for tag in summary.tags for token in self._tokenize(tag)}

        matches: list[str] = sorted(query_tokens & (project_tokens | summary_tokens | tag_tokens))
        overlap_project = len(query_tokens & project_tokens)
        overlap_summary = len(query_tokens & summary_tokens)
        overlap_tags = len(query_tokens & tag_tokens)

        score = (overlap_project * 4.0) + (overlap_tags * 3.0) + (overlap_summary * 2.0)
        if score > 0 and summary.last_updated is not None:
            score += 0.25
        return score, matches

    def _tokenize(self, text: str) -> set[str]:
        """Tokenize free text into a normalized set for cheap overlap scoring."""

        return {match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(text)}
