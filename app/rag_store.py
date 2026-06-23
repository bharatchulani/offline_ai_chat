import asyncio
import json
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from app.ollama_client import OllamaClient


class RagStore:
    def __init__(self, data_dir: Path, ollama: OllamaClient, embedding_model: str) -> None:
        self._data_dir = data_dir
        self._index_path = data_dir / "index.faiss"
        self._metadata_path = data_dir / "metadata.json"
        self._ollama = ollama
        self._embedding_model = embedding_model
        self._index: faiss.Index | None = None
        self._metadata: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        return len(self._metadata)

    def load(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if self._index_path.exists() != self._metadata_path.exists():
            raise RuntimeError("RAG index is incomplete; remove data/index.faiss and data/metadata.json")
        if self._index_path.exists():
            self._index = faiss.read_index(str(self._index_path))
            self._metadata = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            if self._index.ntotal != len(self._metadata):
                raise RuntimeError("RAG index and metadata contain different numbers of records")

    async def add(self, source: str, chunks: list[str]) -> int:
        if not chunks:
            return 0
        embeddings = np.asarray(await self._ollama.embed(self._embedding_model, chunks), dtype="float32")
        if embeddings.ndim != 2:
            raise RuntimeError("Embeddings must be a two-dimensional array")
        faiss.normalize_L2(embeddings)

        async with self._lock:
            if self._index is None:
                self._index = faiss.IndexFlatIP(embeddings.shape[1])
            elif self._index.d != embeddings.shape[1]:
                raise RuntimeError("Embedding dimensions changed; rebuild the index")

            self._index.add(embeddings)
            first_chunk = sum(1 for item in self._metadata if item["source"] == source)
            self._metadata.extend(
                {"source": source, "chunk": first_chunk + offset + 1, "text": text}
                for offset, text in enumerate(chunks)
            )
            self._save()
        return len(chunks)

    async def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if self._index is None or not self._metadata:
            return []
        embedding = np.asarray(await self._ollama.embed(self._embedding_model, [query]), dtype="float32")
        faiss.normalize_L2(embedding)

        async with self._lock:
            scores, indices = self._index.search(embedding, min(top_k, len(self._metadata)))
            return [
                {**self._metadata[index], "score": float(score)}
                for score, index in zip(scores[0], indices[0], strict=True)
                if index >= 0
            ]

    def _save(self) -> None:
        if self._index is None:
            return
        index_temp = self._index_path.with_suffix(".tmp")
        metadata_temp = self._metadata_path.with_suffix(".tmp")
        faiss.write_index(self._index, str(index_temp))
        metadata_temp.write_text(json.dumps(self._metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        index_temp.replace(self._index_path)
        metadata_temp.replace(self._metadata_path)

