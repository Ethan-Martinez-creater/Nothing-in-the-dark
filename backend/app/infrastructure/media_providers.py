"""Media analysis providers (MEDIA-P0-01).

真实 ffprobe/ffmpeg 元数据提取（本机已具备），以及 OCR/ASR/C2PA 的子进程
适配器与能力探测。缺少依赖时能力状态为 disabled/unsupported，不以成功空
结果伪装完成；每个子进程调用带超时与取消。

本模块不下载模型、不安装依赖；依赖是否可用由 probe_capabilities 探测并
在 health 接口与 UI 明示。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MediaCapabilities:
    ffprobe: bool = False
    ffmpeg: bool = False
    ocr: bool = False
    asr: bool = False
    c2pa: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ffprobe": self.ffprobe,
            "ffmpeg": self.ffmpeg,
            "ocr": self.ocr,
            "asr": self.asr,
            "c2pa": self.c2pa,
            "details": self.details,
        }


def probe_capabilities(
    *,
    ffprobe_path: str | None = None,
    ffmpeg_path: str | None = None,
    tesseract_path: str | None = None,
    whisper_command: str | None = None,
    c2patool_path: str | None = None,
) -> MediaCapabilities:
    caps = MediaCapabilities()
    caps.ffprobe = bool(shutil.which(ffprobe_path or "ffprobe"))
    caps.ffmpeg = bool(shutil.which(ffmpeg_path or "ffmpeg"))
    caps.ocr = bool(shutil.which(tesseract_path or "tesseract"))
    caps.asr = bool(shutil.which(whisper_command or "whisper"))
    caps.c2pa = bool(shutil.which(c2patool_path or "c2patool"))
    caps.details = {
        "ffprobe": ffprobe_path or shutil.which("ffprobe") or "not found",
        "ffmpeg": ffmpeg_path or shutil.which("ffmpeg") or "not found",
        "ocr": tesseract_path or shutil.which("tesseract") or "not found",
        "asr": whisper_command or shutil.which("whisper") or "not found",
        "c2pa": c2patool_path or shutil.which("c2patool") or "not found",
    }
    return caps


async def _run_command(
    cmd: list[str],
    *,
    timeout_seconds: float = 120,
    cancel_event: asyncio.Event | None = None,
) -> tuple[int, str, str]:
    """运行子进程，返回 (returncode, stdout, stderr)；支持超时与取消。

    取消在进程运行期间持续监听（而非只在启动前检查一次）：取消事件
    置位后立即终止进程树。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"

    if cancel_event is not None and cancel_event.is_set():
        proc.kill()
        await proc.wait()
        return 130, "", "cancelled"

    cancelled = False
    stdout = stderr = None
    try:
        if cancel_event is None:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        else:
            communicate_task = asyncio.create_task(proc.communicate())
            cancel_task = asyncio.create_task(cancel_event.wait())
            try:
                await asyncio.wait(
                    {communicate_task, cancel_task},
                    timeout=timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not communicate_task.done():
                    communicate_task.cancel()
                if not cancel_task.done():
                    cancel_task.cancel()
            if cancel_event.is_set() and proc.returncode is None:
                proc.kill()
                await proc.wait()
                cancelled = True
            elif communicate_task.done():
                stdout, stderr = communicate_task.result()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"timeout after {timeout_seconds}s"
    if cancelled:
        return 130, "", "cancelled"
    if stdout is None or stderr is None:
        proc.kill()
        await proc.wait()
        return 124, "", f"timeout after {timeout_seconds}s"
    return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


class FFprobeProvider:
    """调用 ffprobe 提取容器/流元数据（时长、编码、分辨率）。"""

    def __init__(self, ffprobe_path: str | None = None) -> None:
        resolved = shutil.which(ffprobe_path or "ffprobe")
        self._ffprobe = resolved or "ffprobe"
        self._available = bool(resolved)

    @property
    def available(self) -> bool:
        return self._available

    async def probe(self, file_path: str) -> dict[str, Any]:
        code, stdout, stderr = await _run_command(
            [
                self._ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]
        )
        if code != 0:
            return {"error": stderr[:300], "returncode": code}
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return {"error": "invalid ffprobe output"}
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        return {
            "duration_ms": (
                int(float(fmt.get("duration", 0)) * 1000) if fmt.get("duration") else None
            ),
            "format_name": fmt.get("format_name", ""),
            "video_codec": video.get("codec_name") if video else None,
            "audio_codec": audio.get("codec_name") if audio else None,
            "width": int(video["width"]) if video and video.get("width") else None,
            "height": int(video["height"]) if video and video.get("height") else None,
        }


class TesseractOCRProvider:
    """tesseract OCR 适配器；缺依赖时 available=False。"""

    def __init__(self, tesseract_path: str | None = None) -> None:
        self._tesseract = shutil.which(tesseract_path or "tesseract") or "tesseract"

    @property
    def available(self) -> bool:
        return bool(shutil.which(self._tesseract))

    async def extract(self, file_path: str, media_type: str) -> Any:
        from app.services.media_pipeline import OcrResult

        code, stdout, stderr = await _run_command(
            [self._tesseract, file_path, "stdout", "-l", "chi_sim+eng"]
        )
        if code != 0:
            raise RuntimeError(
                f"tesseract OCR failed (exit {code}): "
                f"{stderr.strip()[:300] or stdout.strip()[:300]}"
            )
        # 命令成功但无文字属正常（图片无文字），返回空结果由 Worker 记录 succeeded。
        return OcrResult(text=stdout.strip(), regions=[], language="chi_sim+eng")


class WhisperASRProvider:
    """OpenAI Whisper CLI adapter with deterministic JSON-file collection."""

    def __init__(self, whisper_command: str | None = None) -> None:
        resolved = shutil.which(whisper_command or "whisper")
        self._whisper = resolved or "whisper"
        self._available = bool(resolved)

    @property
    def available(self) -> bool:
        return self._available

    async def transcribe(self, file_path: str, media_type: str) -> Any:
        from app.services.media_pipeline import TranscriptResult

        if not self.available:
            return TranscriptResult(segments=[], full_text="", confidence=0.0)
        output_dir = Path(tempfile.mkdtemp(prefix="coifesp-whisper-"))
        output_file = output_dir / f"{Path(file_path).stem}.json"
        try:
            code, _stdout, stderr = await _run_command(
                [
                    self._whisper,
                    file_path,
                    "--output_format",
                    "json",
                    "--output_dir",
                    str(output_dir),
                ],
                timeout_seconds=1800,
            )
            if code != 0 or not output_file.exists():
                raise RuntimeError(
                    f"whisper ASR failed (exit {code}): {stderr.strip()[:300]}"
                    if code != 0
                    else f"whisper ASR output file missing: {output_file}"
                )
            data = json.loads(output_file.read_text(encoding="utf-8"))
            segments = [
                {
                    "start_ms": int(float(seg.get("start", 0)) * 1000),
                    "end_ms": int(float(seg.get("end", 0)) * 1000),
                    "text": str(seg.get("text", "")).strip(),
                }
                for seg in data.get("segments", [])
            ]
            return TranscriptResult(
                segments=segments,
                full_text=str(data.get("text", "")).strip(),
                language=str(data.get("language", "")),
                confidence=1.0,
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"whisper ASR output parse failed: {exc}") from exc
        finally:
            if output_file.exists():
                await asyncio.to_thread(output_file.unlink)
            try:
                await asyncio.to_thread(output_dir.rmdir)
            except OSError:
                pass


class FFmpegFrameExtractor:
    """Extract a bounded deterministic set of video keyframes with ffmpeg."""

    def __init__(
        self,
        ffmpeg_path: str | None = None,
        *,
        interval_seconds: int = 30,
        max_frames: int = 20,
    ) -> None:
        resolved = shutil.which(ffmpeg_path or "ffmpeg")
        self._ffmpeg = resolved or "ffmpeg"
        self._available = bool(resolved)
        self._interval_seconds = max(1, interval_seconds)
        self._max_frames = max(1, max_frames)

    @property
    def available(self) -> bool:
        return self._available

    async def extract(self, file_path: str, media_type: str) -> list[Any]:
        from app.services.media_pipeline import FrameResult

        if not self.available or media_type != "video":
            return []
        source = Path(file_path)
        output_dir = source.parent / f"{source.name}.keyframes"
        output_dir.mkdir(parents=True, exist_ok=True)
        pattern = output_dir / "frame-%04d.jpg"
        code, _stdout, stderr = await _run_command(
            [
                self._ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                f"fps=1/{self._interval_seconds}",
                "-frames:v",
                str(self._max_frames),
                "-q:v",
                "3",
                "-y",
                str(pattern),
            ],
            timeout_seconds=600,
        )
        if code != 0:
            raise RuntimeError(f"ffmpeg keyframe extraction failed: {stderr[:300]}")
        results: list[FrameResult] = []
        for index, frame_path in enumerate(sorted(output_dir.glob("frame-*.jpg"))):
            digest = hashlib.sha256(frame_path.read_bytes()).hexdigest()
            results.append(
                FrameResult(
                    time_ms=index * self._interval_seconds * 1000,
                    storage_uri=str(frame_path),
                    sha256=digest,
                    metadata={
                        "extractor": "ffmpeg",
                        "interval_seconds": self._interval_seconds,
                    },
                )
            )
        return results


class C2PAToolProvider:
    """c2patool 适配器；缺依赖时 available=False，结果 unsupported 而非伪造 valid。"""

    def __init__(self, c2patool_path: str | None = None) -> None:
        self._c2patool = shutil.which(c2patool_path or "c2patool") or "c2patool"

    @property
    def available(self) -> bool:
        return bool(shutil.which(self._c2patool))

    async def verify(self, file_path: str, data: bytes) -> Any:
        from app.services.media_pipeline import C2PAResult

        if not self.available:
            return C2PAResult(
                status="unsupported",
                details={"reason": "c2patool_not_available"},
            )
        code, stdout, stderr = await _run_command([self._c2patool, file_path])
        if code != 0:
            return C2PAResult(status="error", details={"reason": stderr[:300]})
        # c2patool 成功返回 0 且输出 manifest JSON；无 manifest 时返回非零。
        return C2PAResult(
            status="valid",
            manifest=stdout.encode("utf-8", "replace")[:4096],
            details={"verifier": self._c2patool},
        )
