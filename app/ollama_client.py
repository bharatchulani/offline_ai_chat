from typing import Any

import httpx


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        response = await self._client.post("/api/embed", json={"model": model, "input": texts})
        response.raise_for_status()
        embeddings = response.json().get("embeddings")
        if not embeddings or len(embeddings) != len(texts):
            raise RuntimeError("Ollama returned an unexpected embedding response")
        return embeddings

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        content = response.json().get("message", {}).get("content")
        if not content:
            raise RuntimeError("Ollama returned an empty chat response")
        return str(content)

    async def version(self) -> str:
        response = await self._client.get("/api/version")
        response.raise_for_status()
        return str(response.json().get("version", "unknown"))

