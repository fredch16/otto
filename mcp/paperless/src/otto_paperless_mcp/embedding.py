"""Local text embeddings through Ollama (default model: bge-m3, multilingual)."""
from __future__ import annotations

import os

import httpx

DIMENSIONS = 1024  # bge-m3; the index schema is built for this size


class EmbeddingError(Exception):
    pass


class OllamaEmbedder:
    def __init__(self, url: str | None = None, model: str | None = None, *, batch_size: int = 16):
        self.url = (url or os.environ.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
        self.model = model or os.environ.get("OTTO_EMBED_MODEL") or "bge-m3"
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            try:
                response = httpx.post(
                    f"{self.url}/api/embed", json={"model": self.model, "input": batch}, timeout=300
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise EmbeddingError(
                    f"Ollama embedding failed ({error}); is Ollama running with {self.model} pulled?"
                ) from error
            vectors.extend(response.json()["embeddings"])
        if any(len(v) != DIMENSIONS for v in vectors):
            raise EmbeddingError(f"{self.model} does not produce {DIMENSIONS}-dimensional vectors.")
        return vectors
