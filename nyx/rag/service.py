"""High-level RAG indexing and semantic search service for Nyx."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import re
import time

from nyx.config import NyxConfig
from nyx.context.compaction import ContextCompactor
from nyx.rag.embeddings import OllamaEmbedder
from nyx.rag.store import ChromaRagStore, RagChunk, RagSearchHit

_PROJECT_COLLECTION_PREFIX = "nyx-project-"
_INBOX_COLLECTION_NAME = "nyx-inbox"


class RagService:
    """Build and query a project-aware local RAG index."""

    def __init__(
        self,
        config: NyxConfig,
        store: ChromaRagStore,
        embedder: OllamaEmbedder,
        compactor: ContextCompactor | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the service with explicit storage and embedding dependencies."""

        self.config = config
        self.store = store
        self.embedder = embedder
        self.logger = logger or logging.getLogger("nyx.rag.service")
        self.compactor = compactor or ContextCompactor(config=config, logger=self.logger)
        self._last_sync_monotonic = 0.0

    async def ensure_index_current(self) -> None:
        """Refresh the local index when it is stale enough to warrant a rebuild."""

        now = time.monotonic()
        if now - self._last_sync_monotonic < 5:
            return
        await self.rebuild_index()
        self._last_sync_monotonic = now

    async def rebuild_index(self) -> None:
        """Rebuild inbox and project collections from the current notes tree."""

        collection_chunks = await self._build_collection_chunks()
        existing = await self.store.list_collection_names()
        managed_existing = [
            name
            for name in existing
            if name == _INBOX_COLLECTION_NAME or name.startswith(_PROJECT_COLLECTION_PREFIX)
        ]
        desired = set(collection_chunks)
        for collection_name in managed_existing:
            if collection_name not in desired:
                await self.store.delete_collection(collection_name)
        for collection_name, chunks in collection_chunks.items():
            await self.store.replace_collection(collection_name, chunks)

    async def search(
        self,
        query: str,
        *,
        project_name: str | None = None,
        inbox_only: bool = False,
        limit: int = 5,
    ) -> list[RagSearchHit]:
        """Run semantic search against the inbox and/or project collections."""

        await self.ensure_index_current()
        query_embeddings = await self.embedder.embed_texts([query])
        collection_names = await self._resolve_search_collections(
            query=query,
            project_name=project_name,
            inbox_only=inbox_only,
        )
        if not collection_names:
            return []
        return await self.store.query_collections(
            collection_names=collection_names,
            query_embedding=query_embeddings[0],
            n_results=limit,
        )

    async def list_project_names(self) -> list[str]:
        """Return existing project names under the configured notes tree."""

        if not self.config.notes.projects_dir.exists():
            return []
        return sorted(
            child.name
            for child in self.config.notes.projects_dir.iterdir()
            if child.is_dir()
        )

    async def _resolve_search_collections(
        self,
        *,
        query: str,
        project_name: str | None,
        inbox_only: bool,
    ) -> list[str]:
        """Resolve the collection set for one semantic search request."""

        if inbox_only:
            return [_INBOX_COLLECTION_NAME]

        if project_name is not None:
            resolved = await self.resolve_project_name(project_name)
            if resolved is None:
                return []
            return [self.project_collection_name(resolved)]

        ranked_projects = await self.compactor.rank_projects(query, limit=3)
        project_names = [ranked.summary.project_name for ranked in ranked_projects]
        if not project_names:
            project_names = (await self.list_project_names())[:3]
        return [_INBOX_COLLECTION_NAME, *[self.project_collection_name(name) for name in project_names]]

    async def resolve_project_name(self, project_name: str) -> str | None:
        """Resolve a case-insensitive project name to its canonical directory name."""

        for candidate in await self.list_project_names():
            if candidate.casefold() == project_name.casefold():
                return candidate
        return None

    async def _build_collection_chunks(self) -> dict[str, list[RagChunk]]:
        """Scan the notes tree and return fully embedded chunks per collection."""

        collection_texts: dict[str, list[tuple[str, dict[str, str]]]] = {}

        inbox_path = self.config.notes.notes_dir / self.config.notes.inbox_file
        if inbox_path.exists():
            inbox_text = inbox_path.read_text(encoding="utf-8")
            collection_texts[_INBOX_COLLECTION_NAME] = self._chunk_document(
                source_path=inbox_path,
                text=inbox_text,
                project_name=None,
            )
        else:
            collection_texts[_INBOX_COLLECTION_NAME] = []

        for project_name in await self.list_project_names():
            project_path = self.config.notes.projects_dir / project_name
            texts: list[tuple[str, dict[str, str]]] = []
            for source_path in sorted(project_path.glob("*.md")):
                texts.extend(
                    self._chunk_document(
                        source_path=source_path,
                        text=source_path.read_text(encoding="utf-8"),
                        project_name=project_name,
                    )
                )
            collection_texts[self.project_collection_name(project_name)] = texts

        collection_chunks: dict[str, list[RagChunk]] = {}
        for collection_name, entries in collection_texts.items():
            documents = [document for document, metadata in entries if document.strip()]
            metadatas = [metadata for document, metadata in entries if document.strip()]
            if not documents:
                collection_chunks[collection_name] = []
                continue
            embeddings = await self.embedder.embed_texts(documents)
            chunks: list[RagChunk] = []
            for index, (document, metadata) in enumerate(zip(documents, metadatas, strict=True)):
                chunks.append(
                    RagChunk(
                        chunk_id=self._chunk_id(collection_name, metadata["source_path"], index, document),
                        document=document,
                        metadata=metadata,
                        embedding=embeddings[index],
                    )
                )
            collection_chunks[collection_name] = chunks
        return collection_chunks

    def project_collection_name(self, project_name: str) -> str:
        """Return a Chroma-safe collection name for one project."""

        slug = re.sub(r"[^a-z0-9]+", "-", project_name.casefold()).strip("-") or "project"
        digest = hashlib.sha1(project_name.encode("utf-8")).hexdigest()[:8]
        return f"{_PROJECT_COLLECTION_PREFIX}{slug}-{digest}"

    def _chunk_document(
        self,
        *,
        source_path: Path,
        text: str,
        project_name: str | None,
        max_chars: int = 900,
    ) -> list[tuple[str, dict[str, str]]]:
        """Split a markdown document into paragraph-oriented retrieval chunks."""

        normalized = text.strip()
        if not normalized:
            return []

        paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n", normalized) if segment.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = paragraph if not current else f"{current}\n\n{paragraph}"
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = paragraph
            else:
                current = candidate
        if current:
            chunks.append(current)

        metadata_base = {
            "project": project_name or "inbox",
            "file_name": source_path.name,
            "source_path": str(source_path),
        }
        return [
            (
                chunk,
                {
                    **metadata_base,
                    "chunk_index": str(index),
                },
            )
            for index, chunk in enumerate(chunks)
        ]

    def _chunk_id(self, collection_name: str, source_path: str, index: int, document: str) -> str:
        """Return a deterministic identifier for one stored chunk."""

        digest = hashlib.sha1(f"{collection_name}:{source_path}:{index}:{document}".encode("utf-8")).hexdigest()
        return digest
