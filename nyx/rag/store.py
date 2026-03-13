"""Chroma-backed storage primitives for the Nyx RAG system."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Callable

ClientFactory = Callable[[Path], Any]


@dataclass(slots=True)
class RagChunk:
    """One indexed text chunk stored in a Chroma collection."""

    chunk_id: str
    document: str
    metadata: dict[str, Any]
    embedding: list[float]


@dataclass(slots=True)
class RagSearchHit:
    """One semantic-search hit returned from the RAG store."""

    collection_name: str
    document: str
    metadata: dict[str, Any]
    distance: float | None


class ChromaRagStore:
    """Persist and query Nyx RAG collections in a local Chroma database."""

    def __init__(
        self,
        db_path: Path,
        logger: logging.Logger | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        """Initialize the store with a persistent database path."""

        self.db_path = db_path
        self.logger = logger or logging.getLogger("nyx.rag.store")
        self._client_factory = client_factory or self._default_client_factory
        self._client: Any | None = None

    async def list_collection_names(self) -> list[str]:
        """Return all collection names currently present in the local store."""

        return await asyncio.to_thread(self._list_collection_names_sync)

    async def replace_collection(self, collection_name: str, chunks: list[RagChunk]) -> None:
        """Replace one collection with the provided chunks."""

        await asyncio.to_thread(self._replace_collection_sync, collection_name, chunks)

    async def delete_collection(self, collection_name: str) -> None:
        """Delete one collection when it exists."""

        await asyncio.to_thread(self._delete_collection_sync, collection_name)

    async def query_collections(
        self,
        collection_names: list[str],
        query_embedding: list[float],
        n_results: int,
    ) -> list[RagSearchHit]:
        """Query several collections and return globally sorted search hits."""

        return await asyncio.to_thread(
            self._query_collections_sync,
            collection_names,
            query_embedding,
            n_results,
        )

    def _get_client(self) -> Any:
        """Create or reuse the underlying persistent Chroma client."""

        if self._client is None:
            self.db_path.mkdir(parents=True, exist_ok=True)
            self._client = self._client_factory(self.db_path)
        return self._client

    def _list_collection_names_sync(self) -> list[str]:
        """Synchronously list store collections."""

        client = self._get_client()
        collections = client.list_collections()
        names: list[str] = []
        for collection in collections:
            name = getattr(collection, "name", None)
            if isinstance(name, str):
                names.append(name)
        return names

    def _replace_collection_sync(self, collection_name: str, chunks: list[RagChunk]) -> None:
        """Synchronously replace one Chroma collection."""

        client = self._get_client()
        self._delete_collection_sync(collection_name)
        collection = client.get_or_create_collection(name=collection_name)
        if not chunks:
            return
        collection.add(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.document for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
            embeddings=[chunk.embedding for chunk in chunks],
        )

    def _delete_collection_sync(self, collection_name: str) -> None:
        """Synchronously delete a collection while ignoring missing names."""

        client = self._get_client()
        try:
            client.delete_collection(name=collection_name)
        except Exception:
            return

    def _query_collections_sync(
        self,
        collection_names: list[str],
        query_embedding: list[float],
        n_results: int,
    ) -> list[RagSearchHit]:
        """Synchronously query collections and flatten the result rows."""

        client = self._get_client()
        hits: list[RagSearchHit] = []
        for collection_name in collection_names:
            try:
                collection = client.get_collection(name=collection_name)
            except Exception:
                self.logger.debug("Skipping missing Chroma collection '%s'.", collection_name)
                continue

            payload = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
            documents = payload.get("documents", [[]])
            metadatas = payload.get("metadatas", [[]])
            distances = payload.get("distances", [[]])
            for document, metadata, distance in zip(
                documents[0] if documents else [],
                metadatas[0] if metadatas else [],
                distances[0] if distances else [],
                strict=False,
            ):
                hits.append(
                    RagSearchHit(
                        collection_name=collection_name,
                        document=document,
                        metadata=metadata or {},
                        distance=distance,
                    )
                )

        hits.sort(key=lambda hit: hit.distance if hit.distance is not None else float("inf"))
        return hits[:n_results]

    def _default_client_factory(self, db_path: Path) -> Any:
        """Create the production Chroma persistent client lazily."""

        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError(
                "ChromaDB is not installed. Install Nyx with the Phase 8 dependencies before using RAG."
            ) from exc

        return chromadb.PersistentClient(path=str(db_path))
