"""Tests for Phase 9 README frontmatter context compaction."""

from __future__ import annotations

from datetime import date
import logging
from pathlib import Path

import pytest

from nyx.config import load_config
from nyx.context.compaction import ContextCompactor


@pytest.mark.anyio
async def test_compactor_parses_frontmatter_fields(tmp_path: Path) -> None:
    """The compactor should parse summary, tags, and last_updated from README frontmatter."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    project_dir = config.notes.projects_dir / "alpha"
    project_dir.mkdir(parents=True)
    (project_dir / "README.md").write_text(
        """---
summary: "Alpha auth service for token rotation"
last_updated: 2026-03-12
tags: [python, auth, tokens]
---

Body text.
""",
        encoding="utf-8",
    )

    compactor = ContextCompactor(config=config, logger=logging.getLogger("test"))
    summaries = await compactor.list_project_summaries()

    assert len(summaries) == 1
    assert summaries[0].project_name == "alpha"
    assert summaries[0].summary == "Alpha auth service for token rotation"
    assert summaries[0].last_updated == date(2026, 3, 12)
    assert summaries[0].tags == ["python", "auth", "tokens"]


@pytest.mark.anyio
async def test_compactor_ranks_most_relevant_projects_first(tmp_path: Path) -> None:
    """Query ranking should prefer project names/tags/summaries with term overlap."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"

    alpha_dir = config.notes.projects_dir / "alpha-auth"
    beta_dir = config.notes.projects_dir / "beta-ui"
    alpha_dir.mkdir(parents=True)
    beta_dir.mkdir(parents=True)
    (alpha_dir / "README.md").write_text(
        """---
summary: "Authentication and token rotation service"
last_updated: 2026-03-12
tags: [auth, security]
---
""",
        encoding="utf-8",
    )
    (beta_dir / "README.md").write_text(
        """---
summary: "GTK launcher and panel UI"
last_updated: 2026-03-12
tags: [gtk, ui]
---
""",
        encoding="utf-8",
    )

    compactor = ContextCompactor(config=config, logger=logging.getLogger("test"))
    ranked = await compactor.rank_projects("auth token rotation", limit=2)

    assert [item.summary.project_name for item in ranked] == ["alpha-auth", "beta-ui"]
    assert ranked[0].score > ranked[1].score
    assert "auth" in ranked[0].matched_terms
