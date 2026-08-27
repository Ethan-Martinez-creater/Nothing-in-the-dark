"""Multimodal media pipeline domain logic (04).

纯标准库实现的核心：受控下载前的 SSRF URL 校验、magic-bytes MIME 嗅探、
真实文件字节 SHA-256、C2PA 五态检测，以及 OCR/ASR/抽帧/C2PA 的端口化
Provider 抽象。真实 OCR/ASR/视频处理依赖重，由 Provider 隔离；默认实现
不引入 ffmpeg/tesseract，仅做无依赖的基础检测与回退。
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol


class UnsafeUrlError(ValueError):
    """下载 URL 未通过 SSRF / 协议 / 凭据校验。"""


PIPELINE_STAGES = ("download", "probe", "ocr", "asr", "keyframe", "c2pa")
STAGE_STATUSES = ("pending", "running", "succeeded", "failed", "skipped")
DEFAULT_MAX_ATTEMPTS = 3
C2PA_STATUSES = ("valid", "invalid", "not_present", "unsupported", "error")

# 下载限额（可配置，这里给出默认值）。
DEFAULT_LIMITS = {
    "image": 25 * 1024 * 1024,
    "audio": 200 * 1024 * 1024,
    "video": 500 * 1024 * 1024,
    "max_duration_ms": 30 * 60 * 1000,
}


# ---- SSRF URL 校验 --------------------------------------------------------


def is_unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_download_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeUrlError(f"仅允许 HTTPS 下载，实际 scheme={parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("下载 URL 不得包含凭据")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("下载 URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"DNS 解析失败: {host}") from exc
    if not infos:
        raise UnsafeUrlError(f"DNS 无解析结果: {host}")
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if is_unsafe_ip(ip):
            raise UnsafeUrlError(f"拒绝不安全地址 {ip}（{host}）")
    return parsed


# ---- magic bytes MIME 嗅探 -------------------------------------------------


def _sig(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (_sig("ffd8ff"), "image/jpeg"),
    (_sig("89504e470d0a1a0a"), "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "application/riff"),
    (_sig("00000018") + b"ftyp", "video/mp4"),
    (_sig("00000020") + b"ftyp", "video/mp4"),
    (b"ID3", "audio/mpeg"),
    (b"fLaC", "audio/flac"),
    (b"OggS", "audio/ogg"),
]


def sniff_mime_type(head: bytes) -> str | None:
    if not head:
        return None
    for magic, mime in _MAGIC_SIGNATURES:
        if head.startswith(magic):
            if mime == "application/riff":
                if len(head) >= 12 and head[8:12] == b"WEBP":
                    return "image/webp"
                if len(head) >= 12 and head[8:12] == b"WAVE":
                    return "audio/wav"
                return "application/octet-stream"
            return mime
    return None


# ---- 真实文件哈希 ----------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- C2PA 五态检测 ---------------------------------------------------------

_JUMBF_MARKER = b"JUMBF"
_C2PA_MARKER = b"c2pa"


@dataclass(slots=True)
class C2PAResult:
    status: str
    manifest: bytes | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "details": self.details or {}}


def detect_c2pa(data: bytes) -> C2PAResult:
    if not data:
        return C2PAResult(status="error", details={"reason": "empty_file"})
    if _C2PA_MARKER not in data and _JUMBF_MARKER not in data:
        return C2PAResult(status="not_present")
    idx = data.find(_JUMBF_MARKER)
    if idx < 0:
        idx = data.find(_C2PA_MARKER)
    manifest = data[max(0, idx - 4): idx + 64]
    return C2PAResult(
        status="unsupported",
        manifest=manifest,
        details={"reason": "c2pa_detected_but_no_verifier"},
    )


# ---- Provider 端口 ---------------------------------------------------------


@dataclass(slots=True)
class OcrResult:
    text: str
    regions: list[dict[str, Any]]
    language: str = ""


@dataclass(slots=True)
class TranscriptResult:
    segments: list[dict[str, Any]]
    full_text: str
    language: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class FrameResult:
    time_ms: int
    storage_uri: str | None = None
    sha256: str | None = None
    metadata: dict[str, Any] | None = None


class OCRProvider(Protocol):
    async def extract(self, file_path: str, media_type: str) -> OcrResult: ...


class ASRProvider(Protocol):
    async def transcribe(self, file_path: str, media_type: str) -> TranscriptResult: ...


class FrameExtractor(Protocol):
    async def extract(self, file_path: str, media_type: str) -> list[FrameResult]: ...


class C2PAVerifier(Protocol):
    async def verify(self, file_path: str, data: bytes) -> C2PAResult: ...


class NullOCRProvider:
    available = False

    async def extract(self, file_path: str, media_type: str) -> OcrResult:
        return OcrResult(text="", regions=[])


class NullASRProvider:
    available = False

    async def transcribe(self, file_path: str, media_type: str) -> TranscriptResult:
        return TranscriptResult(segments=[], full_text="", confidence=0.0)


class NullFrameExtractor:
    available = False

    async def extract(self, file_path: str, media_type: str) -> list[FrameResult]:
        return []


class ByteC2PAVerifier:
    available = True

    async def verify(self, file_path: str, data: bytes) -> C2PAResult:
        return detect_c2pa(data)


# ---- 图片尺寸探测（标准库，无 PIL） ---------------------------------------


def probe_image_dimensions(data: bytes, mime_type: str | None) -> tuple[int, int] | None:
    """Parse PNG IHDR / JPEG SOF / GIF logical screen for (width, height).

    不信任文件后缀与响应 MIME，只依据 magic bytes 与文件头结构。
    """
    if data.startswith(b"\x89PNG"):
        if len(data) >= 24 and data[12:16] == b"IHDR":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        return None
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 <= n:
                height = int.from_bytes(data[i + 5:i + 7], "big")
                width = int.from_bytes(data[i + 7:i + 9], "big")
                return width, height
            return None
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            return None
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + seg_len
    return None
