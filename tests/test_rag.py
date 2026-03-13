"""Tests for the Phase 8 RAG service and module."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from nyx.config import load_config
from nyx.modules.rag import RagModule
from nyx.providers.base import ProviderQueryResult
from nyx.rag.service import RagService
from nyx.rag.store import RagChunk, RagSearchHit


@dataclass
class FakeEmbedder:
    """Deterministic embedding stub used by RAG tests."""

    seen_texts: list[list[str]] | None = None

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return a simple deterministic embedding vector per text."""

        if self.seen_texts is None:
            self.seen_texts = []
        self.seen_texts.append(list(texts))
        return [[float(index + 1), float(len(text))] for index, text in enumerate(texts)]


class FakeStore:
    """In-memory Chroma-like store stub for RAG tests."""

    def __init__(self) -> None:
        self.collections: dict[str, list[RagChunk]] = {}
        self.last_query_collections: list[str] | None = None

    async def list_collection_names(self) -> list[str]:
        """Return collection names stored in memory."""

        return list(self.collections)

    async def replace_collection(self, collection_name: str, chunks: list[RagChunk]) -> None:
        """Replace one in-memory collection."""

        self.collections[collection_name] = list(chunks)

    async def delete_collection(self, collection_name: str) -> None:
        """Delete one in-memory collection."""

        self.collections.pop(collection_name, None)

    async def query_collections(
        self,
        collection_names: list[str],
        query_embedding: list[float],
        n_results: int,
    ) -> list[RagSearchHit]:
        """Return simple deterministic hits from stored chunks."""

        del query_embedding
        self.last_query_collections = list(collection_names)
        hits: list[RagSearchHit] = []
        for collection_name in collection_names:
            for chunk in self.collections.get(collection_name, []):
                hits.append(
                    RagSearchHit(
                        collection_name=collection_name,
                        document=chunk.document,
                        metadata=chunk.metadata,
                        distance=0.1,
                    )
                )
        return hits[:n_results]


@dataclass
class FakeProviderRegistry:
    """Minimal registry stub for provider-planned RAG requests."""

    result: ProviderQueryResult
    seen_prompt: str | None = None
    seen_context: dict[str, Any] | None = None

    async def query(
        self,
        prompt: str,
        context: dict[str, Any],
        preferred_provider_name: str | None = None,
    ) -> ProviderQueryResult:
        """Return a deterministic provider planning result."""

        del preferred_provider_name
        self.seen_prompt = prompt
        self.seen_context = context
        return self.result


@pytest.mark.anyio
async def test_rag_service_rebuilds_project_and_inbox_collections(tmp_path: Path) -> None:
    """The RAG service should build inbox and per-project collections."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    config.rag.db_path = tmp_path / "rag"

    alpha_dir = config.notes.projects_dir / "alpha"
    alpha_dir.mkdir(parents=True)
    (config.notes.notes_dir / "inbox.md").write_text("remember milk\n", encoding="utf-8")
    (alpha_dir / "notes.md").write_text("alpha release checklist\n", encoding="utf-8")
    (alpha_dir / "tasks.md").write_text("- [ ] ship alpha\n", encoding="utf-8")

    store = FakeStore()
    embedder = FakeEmbedder()
    service = RagService(config=config, store=store, embedder=embedder, logger=logging.getLogger("test"))

    await service.rebuild_index()

    assert "nyx-inbox" in store.collections
    assert any(name.startswith("nyx-project-alpha-") for name in store.collections)
    assert embedder.seen_texts is not None
    assert any("remember milk" in " ".join(batch) for batch in embedder.seen_texts)


@pytest.mark.anyio
async def test_rag_module_returns_formatted_hits(tmp_path: Path) -> None:
    """The explicit RAG module should return formatted semantic-search results."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    config.rag.db_path = tmp_path / "rag"

    alpha_dir = config.notes.projects_dir / "alpha"
    alpha_dir.mkdir(parents=True)
    (alpha_dir / "notes.md").write_text("remember the auth token rotation policy\n", encoding="utf-8")

    store = FakeStore()
    embedder = FakeEmbedder()
    service = RagService(config=config, store=store, embedder=embedder, logger=logging.getLogger("test"))
    provider_registry = FakeProviderRegistry(
        result=ProviderQueryResult(
            provider_name="codex-cli",
            provider_type="subprocess-cli",
            model_name=None,
            text='{"operation":"search_project","arguments":{"project":"alpha","query":"auth token rotation"}}',
            fallback_used=False,
        )
    )
    module = RagModule(
        config=config,
        provider_registry=provider_registry,
        rag_service=service,
        logger=logging.getLogger("test"),
    )

    result = await module.handle("search project alpha for auth token rotation")

    assert result.used_model == "codex-cli"
    assert "Top matches for 'auth token rotation':" in result.response_text
    assert "[alpha]" in result.response_text
    assert "auth token rotation policy" in result.response_text


@pytest.mark.anyio
async def test_rag_service_uses_context_compaction_for_global_search(tmp_path: Path) -> None:
    """Global search should pre-filter to the top 1-3 ranked projects plus inbox."""

    config = load_config(tmp_path / "missing.toml")
    config.notes.notes_dir = tmp_path / "notes"
    config.notes.projects_dir = config.notes.notes_dir / "projects"
    config.rag.db_path = tmp_path / "rag"

    for project_name, summary in {
        "alpha-auth": "Authentication and token rotation service",
        "beta-ui": "GTK launcher and panel UI",
        "gamma-web": "Browser and web search helpers",
        "delta-notes": "Plain markdown note workflows",
    }.items():
        project_dir = config.notes.projects_dir / project_name
        project_dir.mkdir(parents=True)
        (project_dir / "README.md").write_text(
            f"---\nsummary: \"{summary}\"\nlast_updated: 2026-03-12\ntags: [{project_name.split('-')[0]}]\n---\n",
            encoding="utf-8",
        )
        (project_dir / "notes.md").write_text(summary + "\n", encoding="utf-8")
    (config.notes.notes_dir / "inbox.md").write_text("remember oat milk\n", encoding="utf-8")

    store = FakeStore()
    embedder = FakeEmbedder()
    service = RagService(config=config, store=store, embedder=embedder, logger=logging.getLogger("test"))

    await service.rebuild_index()
    hits = await service.search("auth token rotation")

    assert hits
    assert store.last_query_collections is not None
    assert "nyx-inbox" in store.last_query_collections
    project_collections = [name for name in store.last_query_collections if name != "nyx-inbox"]
    assert 1 <= len(project_collections) <= 3
    assert any("alpha-auth" in name for name in project_collections)


def test_rag_module_matcher_is_conservative() -> None:
    """The RAG matcher should catch explicit local search prompts only."""

    assert RagModule.matches_request("search notes for auth token rotation") is True
    assert RagModule.matches_request("find in projects where release notes live") is True
    assert RagModule.matches_request("search inbox for coffee filters") is True
    assert RagModule.matches_request("write release notes for alpha") is False
