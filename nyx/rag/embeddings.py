"""Async Ollama embedding client for Nyx RAG indexing."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

import httpx

from nyx.config import NyxConfig

AsyncClientFactory = Callable[..., httpx.AsyncClient]


class OllamaEmbedder:
    """Generate embeddings from a local Ollama server using the embed API."""

    def __init__(
        self,
        config: NyxConfig,
        logger: logging.Logger | None = None,
        client_factory: AsyncClientFactory | None = None,
    ) -> None:
        """Initialize the embedder from Nyx configuration."""

        self.config = config
        self.logger = logger or logging.getLogger("nyx.rag.embedder")
        self._client_factory = client_factory or httpx.AsyncClient
        self._host = self._resolve_ollama_host()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings for the provided texts using Ollama's embed API."""

        if not texts:
            return []

        async with self._client_factory(base_url=self._host, timeout=30.0) as client:
            try:
                response = await client.post(
                    "/api/embed",
                    json={
                        "model": self.config.rag.embed_model,
                        "input": texts,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Ollama embed API unavailable at {self._host} for model '{self.config.rag.embed_model}': {exc}"
                ) from exc
            payload = response.json()

        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ValueError("Ollama embed API returned an unexpected embeddings payload.")
        return embeddings

    def _resolve_ollama_host(self) -> str:
        """Resolve the Ollama host from configured providers or defaults."""

        for provider in self.config.models.providers:
            if provider.type == "ollama":
                host = provider.options.get("host")
                if isinstance(host, str) and host:
                    return host.rstrip("/")
        return "http://localhost:11434"
