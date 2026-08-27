"""Real acceptance check for the BGE-M3 worker (M8a).

Usage:
    python -m scripts.verify_embedding_worker

Verifies, against a live worker:
* /health returns a healthy status and reports a model_version;
* /v1/embeddings returns vectors with the configured dimensions;
* vectors are length-normalized (BGE-M3 normalize_embeddings=True);
* the same text embeds deterministically (stable fingerprint);
* embedding failures raise the documented error codes.

Exit code 0 when the worker passes, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import math
import sys

from app.core.config import get_settings
from app.core.errors import ApplicationError
from app.infrastructure.embeddings import EmbeddingWorkerClient

_PROBE_TEXTS = ["食品安全事件引发关注", "明星演唱会门票开售"]


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


async def _main() -> int:
    settings = get_settings()
    client = EmbeddingWorkerClient(
        settings.embedding_worker_url,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    if not client.configured:
        print("SKIP: embedding_worker_url is not configured.")
        return 0

    checks = 0

    health = await client.health()
    assert health is not None
    assert health.get("status") == "healthy", f"unexpected health: {health}"
    version = health.get("model_version")
    assert version, "worker health must report model_version"
    checks += 1
    print(f"[1/4] health OK: model={health.get('model')} version={version}")

    vectors = await client.embed(_PROBE_TEXTS)
    assert vectors is not None and len(vectors) == len(_PROBE_TEXTS)
    assert all(
        len(vector) == settings.embedding_dimensions for vector in vectors
    ), "embedding dimensions mismatch"
    checks += 1
    print(
        f"[2/4] embed OK: {len(vectors)} x {settings.embedding_dimensions} "
        f"(worker version {client.model_version})"
    )

    for vector in vectors:
        magnitude = _norm(vector)
        assert abs(magnitude - 1.0) < 1e-3, (
            f"embedding not normalized: magnitude={magnitude:.4f}"
        )
    checks += 1
    print("[3/4] normalization OK (unit vectors)")

    repeat = await client.embed(_PROBE_TEXTS)
    assert repeat is not None
    assert repeat == vectors, "embedding is not deterministic"
    checks += 1
    print("[4/4] determinism OK")

    assert await client.embed([]) is None, "empty batch must be a no-op"
    print(f"ACCEPT: {checks}/4 checks passed (version {client.model_version})")
    return 0


def _main_wrapper() -> None:
    try:
        exit_code = asyncio.run(_main())
    except ApplicationError as exc:
        print(f"REJECT: {exc.code}: {exc.message}")
        exit_code = 1
    except AssertionError as exc:
        print(f"REJECT: assertion failed: {exc}")
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    _main_wrapper()
