"""Async client for the isolated sentiment worker (Erlangshen RoBERTa).

The worker runs the Chinese sentiment model in its own process so the
FastAPI process never touches torch. Each item returned is::

    {"label": "positive" | "negative",
     "score": float,           # P(positive) - P(negative) in [-1, 1]
     "confidence": float,      # max class probability
     "probabilities": {"negative": float, "positive": float}}

``classify`` returns ``None`` (never raises) when the client is not
configured; it raises :class:`ApplicationError` when the worker is
configured but unreachable so callers can fall back to the dictionary.
"""

from __future__ import annotations

import httpx

from app.core.errors import ApplicationError


class SentimentWorkerClient:
    """Async client for the isolated Erlangshen sentiment worker."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    async def classify(
        self,
        texts: list[str],
    ) -> list[dict[str, object]] | None:
        if not self.configured or not texts:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/v1/sentiment",
                    json={"texts": texts},
                )
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ApplicationError(
                "The sentiment worker is unavailable",
                code="sentiment_worker_unavailable",
            ) from exc
        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != len(texts):
            raise ApplicationError(
                "The sentiment worker returned an invalid batch",
                code="invalid_sentiment_response",
            )
        return [dict(item) for item in results]
