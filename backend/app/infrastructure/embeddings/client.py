from __future__ import annotations

import httpx

from app.core.errors import ApplicationError


class EmbeddingWorkerClient:
    """Async client for the isolated BGE-M3 worker."""

    def __init__(
        self,
        base_url: str,
        *,
        dimensions: int,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._dimensions = dimensions
        self._timeout = timeout_seconds
        self._model_version: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    @property
    def model_version(self) -> str | None:
        """Version reported by the worker on the last embed/health call."""
        return self._model_version

    async def health(self) -> dict[str, object] | None:
        """Probe the worker; returns the reported model version map.

        ``None`` when the client is not configured. Raises
        ``embedding_worker_unavailable`` on any HTTP / timeout failure.
        """
        if not self.configured:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/health")
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ApplicationError(
                "The embedding worker is unavailable",
                code="embedding_worker_unavailable",
            ) from exc
        payload = response.json()
        version = payload.get("model_version")
        if version:
            self._model_version = str(version)
        return payload

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.configured or not texts:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/v1/embeddings",
                    json={"texts": texts},
                )
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ApplicationError(
                "The embedding worker is unavailable",
                code="embedding_worker_unavailable",
            ) from exc
        payload = response.json()
        version = payload.get("model_version")
        if version:
            self._model_version = str(version)
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ApplicationError(
                "The embedding worker returned an invalid batch",
                code="invalid_embedding_response",
            )
        vectors = [[float(value) for value in vector] for vector in embeddings]
        if any(len(vector) != self._dimensions for vector in vectors):
            raise ApplicationError(
                "The embedding worker returned unexpected dimensions",
                code="invalid_embedding_dimensions",
            )
        return vectors
