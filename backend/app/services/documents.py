from __future__ import annotations

import hashlib
import io
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

from app.core.errors import ApplicationError

SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "text/markdown",
    "text/plain",
    "text/html",
    "application/xhtml+xml",
}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)


def extract_document_text(
    content: bytes,
    *,
    filename: str,
    media_type: str,
) -> str:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ApplicationError(
            "Document exceeds the 20 MiB upload limit",
            code="document_too_large",
        )
    resolved_type = _resolve_media_type(filename, media_type)
    if resolved_type not in SUPPORTED_MEDIA_TYPES:
        raise ApplicationError(
            f"Unsupported document media type: {resolved_type}",
            code="unsupported_document_type",
        )
    try:
        if resolved_type == "application/pdf":
            reader = PdfReader(io.BytesIO(content))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            decoded = content.decode("utf-8-sig")
            if resolved_type in {"text/html", "application/xhtml+xml"}:
                parser = _TextExtractor()
                parser.feed(decoded)
                text = "\n".join(parser.parts)
            else:
                text = decoded
    except (UnicodeDecodeError, ValueError) as exc:
        raise ApplicationError(
            "The uploaded document could not be parsed",
            code="document_parse_failed",
        ) from exc
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        raise ApplicationError(
            "The uploaded document does not contain extractable text",
            code="document_empty",
        )
    return normalized


def chunk_document(
    text: str,
    *,
    chunk_size: int = 1_200,
    overlap: int = 200,
) -> list[str]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            start = 0
            while start < len(paragraph):
                piece = paragraph[start : start + chunk_size].strip()
                if piece:
                    chunks.append(piece)
                start += chunk_size - overlap
            continue
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= chunk_size:
            buffer = candidate
            continue
        chunks.append(buffer)
        prefix = buffer[-overlap:].strip()
        buffer = f"{prefix}\n\n{paragraph}".strip()
    if buffer:
        chunks.append(buffer)
    return chunks


def document_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _resolve_media_type(filename: str, media_type: str) -> str:
    if media_type and media_type != "application/octet-stream":
        return media_type.split(";", maxsplit=1)[0].strip().lower()
    suffix = Path(filename).suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
    }.get(suffix, "application/octet-stream")
