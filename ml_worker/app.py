from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from typing import Any

import torch
from fastapi import FastAPI
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from transformers import BertForSequenceClassification, BertTokenizer

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
# Version of the embedding model in effect. Injected by the deployment
# (e.g. pinned model revision); defaults to a short hash of the model name
# so a model swap changes the reported version.
MODEL_VERSION = os.getenv(
    "EMBEDDING_MODEL_VERSION",
    hashlib.sha256(MODEL_NAME.encode("utf-8")).hexdigest()[:8],
)
SENTIMENT_MODEL_NAME = os.getenv(
    "SENTIMENT_MODEL",
    r"E:\Graduate_work_folder\rumor_detection\model\Erlangshen-Roberta-110M-Sentiment",
)
DEVICE_SETTING = os.getenv("EMBEDDING_DEVICE", "auto")
SENTIMENT_DEVICE_SETTING = os.getenv("SENTIMENT_DEVICE", DEVICE_SETTING)
INITIAL_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "4"))
SENTIMENT_BATCH_SIZE = int(os.getenv("SENTIMENT_BATCH_SIZE", "16"))


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)


class EmbeddingResponse(BaseModel):
    model: str
    model_version: str
    device: str
    dimensions: int
    embeddings: list[list[float]]


class SentimentRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)


class SentimentItem(BaseModel):
    label: str
    score: float
    confidence: float
    probabilities: dict[str, float]


class SentimentResponse(BaseModel):
    model: str
    device: str
    results: list[SentimentItem]


class BgeM3Runtime:
    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._device = self._resolve_device()
        self._lock = asyncio.Lock()

    @property
    def device(self) -> str:
        return self._device

    def _resolve_device(self) -> str:
        if DEVICE_SETTING != "auto":
            return DEVICE_SETTING
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(MODEL_NAME, device=self._device)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with self._lock:
            return await asyncio.to_thread(self._embed_with_fallback, texts)

    def _embed_with_fallback(self, texts: list[str]) -> list[list[float]]:
        batch_size = max(1, INITIAL_BATCH_SIZE)
        while True:
            try:
                vectors = self._load().encode(
                    texts,
                    batch_size=batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                return vectors.tolist()
            except torch.cuda.OutOfMemoryError:
                if self._device != "cuda":
                    raise
                torch.cuda.empty_cache()
                if batch_size > 1:
                    batch_size = max(1, batch_size // 2)
                    continue
                self._model = None
                self._device = "cpu"
                batch_size = 1


class ErlangshenSentimentRuntime:
    """Lazily loaded Erlangshen-Roberta-110M-Sentiment classifier.

    The model is a binary (positive/negative) Chinese sentiment classifier;
    the worker returns both class probabilities so the caller can map the
    low-confidence band to neutral. Falls back to CPU on CUDA OOM.
    """

    def __init__(self) -> None:
        self._model: BertForSequenceClassification | None = None
        self._tokenizer: BertTokenizer | None = None
        self._device = self._resolve_device()
        self._lock = asyncio.Lock()

    @property
    def device(self) -> str:
        return self._device

    def _resolve_device(self) -> str:
        if SENTIMENT_DEVICE_SETTING != "auto":
            return SENTIMENT_DEVICE_SETTING
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _load(self) -> tuple[BertForSequenceClassification, BertTokenizer]:
        if self._model is None:
            tokenizer = BertTokenizer.from_pretrained(SENTIMENT_MODEL_NAME)
            model = BertForSequenceClassification.from_pretrained(
                SENTIMENT_MODEL_NAME
            )
            model.eval()
            model.to(self._device)
            self._tokenizer = tokenizer
            self._model = model
        return self._model, self._tokenizer  # type: ignore[return-value]

    async def classify(self, texts: list[str]) -> list[dict[str, object]]:
        async with self._lock:
            return await asyncio.to_thread(self._classify_with_fallback, texts)

    def _classify_with_fallback(
        self,
        texts: list[str],
    ) -> list[dict[str, object]]:
        batch_size = max(1, SENTIMENT_BATCH_SIZE)
        results: list[dict[str, object]] = []
        with torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                results.extend(self._classify_batch(batch))
        return results

    def _classify_batch(self, texts: list[str]) -> list[dict[str, object]]:
        model, tokenizer = self._load()
        encoded = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="pt",
        )
        try:
            encoded = {key: value.to(self._device) for key, value in encoded.items()}
            logits = model(**encoded).logits
        except torch.cuda.OutOfMemoryError:
            if self._device != "cuda":
                raise
            torch.cuda.empty_cache()
            self._model = None
            self._tokenizer = None
            self._device = "cpu"
            model, tokenizer = self._load()
            encoded = tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=256,
                return_tensors="pt",
            )
            logits = model(**encoded).logits
        probabilities = torch.softmax(logits, dim=-1)
        positive = probabilities[:, 1]
        negative_probs = probabilities[:, 0].tolist()
        positive_probs = positive.tolist()
        confidences = probabilities.max(dim=-1).values.tolist()
        scores = (torch.tensor(positive_probs) * 2 - 1).tolist()
        items: list[dict[str, object]] = []
        for index, text in enumerate(texts):
            positive_prob = positive_probs[index]
            items.append(
                {
                    "label": "positive" if positive_prob >= 0.5 else "negative",
                    "score": round(float(scores[index]), 4),
                    "confidence": round(float(confidences[index]), 4),
                    "probabilities": {
                        "negative": round(float(negative_probs[index]), 4),
                        "positive": round(float(positive_prob), 4),
                    },
                }
            )
        return items


runtime = BgeM3Runtime()
sentiment_runtime = ErlangshenSentimentRuntime()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    yield
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


app = FastAPI(
    title="COIFESP ML Worker",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "device": runtime.device,
        "sentiment_model": SENTIMENT_MODEL_NAME,
        "sentiment_device": sentiment_runtime.device,
    }


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
    vectors = await runtime.embed(request.texts)
    dimensions = len(vectors[0]) if vectors else 0
    return EmbeddingResponse(
        model=MODEL_NAME,
        model_version=MODEL_VERSION,
        device=runtime.device,
        dimensions=dimensions,
        embeddings=vectors,
    )


@app.post("/v1/sentiment", response_model=SentimentResponse)
async def sentiment(request: SentimentRequest) -> SentimentResponse:
    results = await sentiment_runtime.classify(request.texts)
    return SentimentResponse(
        model=SENTIMENT_MODEL_NAME,
        device=sentiment_runtime.device,
        results=results,
    )
