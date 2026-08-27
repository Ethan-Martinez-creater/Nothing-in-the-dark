"""M8a: EmbeddingWorkerClient health probe, model version tracking and
response validation against a mocked worker."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import ApplicationError
from app.infrastructure.embeddings.client import EmbeddingWorkerClient


def _client_with_handler(monkeypatch, handler) -> EmbeddingWorkerClient:
    original_async_client = httpx.AsyncClient  # capture before patching

    def fake_async_client(*, timeout: float, **kwargs) -> httpx.AsyncClient:
        return original_async_client(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            **kwargs,
        )

    monkeypatch.setattr(
        "app.infrastructure.embeddings.client.httpx.AsyncClient",
        fake_async_client,
    )
    return EmbeddingWorkerClient(
        "http://worker:8000",
        dimensions=1024,
        timeout_seconds=5.0,
    )


def _vector(length: int = 1024) -> list[float]:
    return [0.5] * length


async def test_health_reports_model_version(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "status": "healthy",
                "model": "BAAI/bge-m3",
                "model_version": "v1.2.3",
                "device": "cpu",
            },
        )

    client = _client_with_handler(monkeypatch, handler)
    health = await client.health()
    assert health["status"] == "healthy"
    assert client.model_version == "v1.2.3"


async def test_health_unavailable_raises(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with_handler(monkeypatch, handler)
    with pytest.raises(ApplicationError) as exc:
        await client.health()
    assert exc.value.code == "embedding_worker_unavailable"


async def test_embed_tracks_version_and_returns_vectors(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "BAAI/bge-m3",
                "model_version": "v2.0.0",
                "device": "cpu",
                "dimensions": 1024,
                "embeddings": [_vector() for _ in payload["texts"]],
            },
        )

    client = _client_with_handler(monkeypatch, handler)
    vectors = await client.embed(["文本一", "文本二"])
    assert vectors is not None and len(vectors) == 2
    assert client.model_version == "v2.0.0"


async def test_embed_dimension_mismatch_rejected(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "BAAI/bge-m3",
                "model_version": "v1",
                "dimensions": 512,
                "embeddings": [[0.5] * 512],
            },
        )

    client = _client_with_handler(monkeypatch, handler)
    with pytest.raises(ApplicationError) as exc:
        await client.embed(["文本"])
    assert exc.value.code == "invalid_embedding_dimensions"


async def test_embed_batch_length_mismatch_rejected(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "BAAI/bge-m3",
                "model_version": "v1",
                "dimensions": 1024,
                "embeddings": [_vector()],  # one vector for two texts
            },
        )

    client = _client_with_handler(monkeypatch, handler)
    with pytest.raises(ApplicationError) as exc:
        await client.embed(["文本一", "文本二"])
    assert exc.value.code == "invalid_embedding_response"


async def test_embed_empty_batch_is_noop(monkeypatch) -> None:
    client = _client_with_handler(
        monkeypatch, lambda request: httpx.Response(500)
    )
    assert await client.embed([]) is None
    assert client.model_version is None
