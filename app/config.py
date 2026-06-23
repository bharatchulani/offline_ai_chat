from dataclasses import dataclass
from pathlib import Path
import os


def _integer(name: str, default: int) -> int:
    value = int(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    ollama_url: str
    chat_model: str
    embedding_model: str
    data_dir: Path
    chunk_size: int
    chunk_overlap: int
    top_k: int
    parquet_dir: Path
    analytics_max_rows: int

    @classmethod
    def from_environment(cls) -> "Settings":
        chunk_size = _integer("RAG_CHUNK_SIZE", 1000)
        chunk_overlap = _integer("RAG_CHUNK_OVERLAP", 150)
        if chunk_overlap >= chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE")

        return cls(
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            chat_model=os.getenv("OLLAMA_CHAT_MODEL", "qwen3:8b"),
            embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            data_dir=Path(os.getenv("RAG_DATA_DIR", "data")).resolve(),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            top_k=_integer("RAG_TOP_K", 4),
            parquet_dir=Path(os.getenv("PARQUET_DATA_DIR", "data/parquet")).resolve(),
            analytics_max_rows=_integer("ANALYTICS_MAX_ROWS", 1000),
        )
