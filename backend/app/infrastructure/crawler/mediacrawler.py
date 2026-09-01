from __future__ import annotations

import asyncio
import codecs
import json
import logging
import math
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.application.ports.crawler import CrawlRequest
from app.core.errors import (
    ApplicationError,
    CrawlerConfigurationError,
    CrawlerExecutionError,
)
from app.harness.cancel import crawl_cancelled, current_cancel_event
from app.services.crawl_coverage import fetch_limit_for

CommandRunner = Callable[..., Awaitable[tuple[int, str, str]]]

_COOKIE_ENV = "COIFESP_MEDIACRAWLER_COOKIES"
_CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")

_WEIBO_ACCOUNT_TRADE_RE = re.compile(
    r"(?:出|卖|收|换|估).{0,6}(?:号|账号)|"
    r"(?:账号|游戏号).{0,8}(?:出售|交易|换绑|估价)|"
    r"(?:自抽号|初始号|成品号|科技号|低价出|白菜价出|找回包赔)"
)
_WEIBO_SALES_SIGNAL_RE = re.compile(
    r"(?:明盘|带价|私聊|私信|走平台|可换绑|包赔|接单|代充|返利|加微|vx)"
)

PLATFORM_CODES = {
    "weibo": "wb",
    "bilibili": "bili",
    "tieba": "tieba",
    "zhihu": "zhihu",
    "douyin": "dy",
}


def _is_weibo_marketing_noise(post: dict[str, object]) -> bool:
    """Identify strong account-trading/lead-generation posts conservatively."""
    text = "\n".join(
        str(value or "")
        for value in (post.get("title"), post.get("content"))
    )
    account_trade = bool(_WEIBO_ACCOUNT_TRADE_RE.search(text))
    sales_signal = bool(_WEIBO_SALES_SIGNAL_RE.search(text))
    # Direct account-trade phrases are already high precision. Generic sales
    # terms are only considered noise when paired with a transaction signal.
    return account_trade or (
        sales_signal
        and bool(re.search(r"(?:出售|交易|低价|优惠|代理|推广)", text))
    )


@dataclass(frozen=True, slots=True)
class MediaCrawlerConfig:
    root: Path
    output_root: Path
    python_executable: Path = Path(sys.executable)
    entrypoint: Path | None = None
    login_type: str = "qrcode"
    headless: bool = False
    include_comments: bool = True
    max_comments_per_post: int = 10
    timeout_seconds: float = 1800
    max_output_runs: int = 100
    usage_mode: str = "research"
    weibo_cookies: str = ""
    bilibili_cookies: str = ""
    tieba_cookies: str = ""
    zhihu_cookies: str = ""
    douyin_cookies: str = ""


# M15: 子进程环境白名单——禁止爬虫读取宿主完整环境（防 .env/密钥泄漏）。
_SANDBOX_ENV_PREFIXES = (
    "COIFESP_",
    "MEDIACRAWLER_",
    "LLM_",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "PATHEXT",
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    # Windows 用户身份变量：getpass.getuser() 在 USERNAME/USER/LOGNAME/
    # LNAME 全部缺失时会 fallback 到 import pwd（POSIX 模块），导致
    # MediaCrawler 在 Windows 子进程内启动即失败。
    "USERNAME",
    "USER",
    "LOGNAME",
    "LNAME",
    "COMPUTERNAME",
    # Playwright 需要 APPDATA/LOCALAPPDATA 定位 ms-playwright 浏览器安装；
    # 缺失时 channel="chrome" 与 bundled chromium 都会报 "not found"。
    "APPDATA",
    "LOCALAPPDATA",
)


async def _run_command(
    command: Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    cancel_event: asyncio.Event | None = None,
    process_environment: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if any(key.startswith(prefix) for prefix in _SANDBOX_ENV_PREFIXES)
    }
    if process_environment:
        environment.update(process_environment)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_stream(
        stream: asyncio.StreamReader | None,
        *,
        error: bool,
    ) -> str:
        if stream is None:
            return ""
        parts: list[str] = []
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while chunk := await stream.read(16 * 1024):
            decoded = decoder.decode(chunk)
            parts.append(decoded)
            target = sys.stderr if error else sys.stdout
            print(decoded, end="", file=target, flush=True)
        final = decoder.decode(b"", final=True)
        if final:
            parts.append(final)
            target = sys.stderr if error else sys.stdout
            print(final, end="", file=target, flush=True)
        return "".join(parts)

    stdout_task = asyncio.create_task(read_stream(process.stdout, error=False))
    stderr_task = asyncio.create_task(read_stream(process.stderr, error=True))
    cancel = cancel_event or current_cancel_event()
    try:
        if cancel is None:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        else:
            wait_task = asyncio.create_task(process.wait())
            cancel_task = asyncio.create_task(cancel.wait())
            try:
                done, pending = await asyncio.wait(
                    {wait_task, cancel_task},
                    timeout=timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for leftover in (wait_task, cancel_task):
                    if not leftover.done():
                        leftover.cancel()
            if cancel.is_set() and process.returncode is None:
                await _kill_process_tree(process)
                await asyncio.shield(process.wait())
                await asyncio.gather(
                    stdout_task, stderr_task, return_exceptions=True
                )
                raise ApplicationError(
                    "Social crawl was cancelled",
                    code="tool_cancelled",
                )
            if not done:
                if process.returncode is None:
                    await _kill_process_tree(process)
                    await process.wait()
                await asyncio.gather(
                    stdout_task, stderr_task, return_exceptions=True
                )
                raise CrawlerExecutionError(
                    f"MediaCrawler exceeded the {timeout_seconds:g}-second timeout"
                )
    except TimeoutError:
        if process.returncode is None:
            await _kill_process_tree(process)
            await process.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise CrawlerExecutionError(
            f"MediaCrawler exceeded the {timeout_seconds:g}-second timeout"
        ) from None
    except asyncio.CancelledError:
        if process.returncode is None:
            await _kill_process_tree(process)
            await asyncio.shield(process.wait())
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise ApplicationError(
            "Social crawl was cancelled",
            code="tool_cancelled",
        ) from None
    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    return (
        process.returncode or 0,
        stdout,
        stderr,
    )


async def _kill_process_tree(process: Any) -> None:
    """终止整个进程树，避免取消/超时后残留爬虫子进程（M15）。

    Windows 下 taskkill /T /F 兜底进程组内的孙进程；非 Windows 依赖
    create_subprocess_exec 的默认行为 + kill。
    """
    try:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=10)
    except Exception:
        logger = __import__("logging").getLogger(__name__)
        logger.exception("crawler process kill failed")
    if os.name == "nt":
        try:
            await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            pass


class MediaCrawlerAdapter:
    """Run MediaCrawler behind a bounded, normalized application port."""

    def __init__(
        self,
        config: MediaCrawlerConfig,
        *,
        command_runner: CommandRunner = _run_command,
    ) -> None:
        self._config = config
        self._command_runner = command_runner
        self._output_lock = asyncio.Lock()

    async def collect(self, request: CrawlRequest) -> list[dict[str, object]]:
        self._validate_installation()
        async with self._output_lock:
            self._validate_output_capacity()
            run_root = self._config.output_root / str(uuid4())
            run_root.mkdir(parents=True, exist_ok=False)
        cancel = request.cancel_event or current_cancel_event()

        posts: list[dict[str, object]] = []
        for platform in request.platforms:
            if crawl_cancelled(cancel):
                raise ApplicationError(
                    "Social crawl was cancelled",
                    code="tool_cancelled",
                )
            if platform not in PLATFORM_CODES:
                raise CrawlerConfigurationError(
                    f"Unsupported platform '{platform}'. "
                    f"Configured platforms: {', '.join(PLATFORM_CODES)}"
                )
            # 每平台一个 MediaCrawler process：多组关键词逗号分隔一次命令
            # （上游 --keywords 支持逗号分隔，进程内顺序搜索，复用登录态
            # 与浏览器上下文；避免"一组关键词 = 一次浏览器全流程"的启动
            # 开销）。结果按 native_id 去重合并。
            keywords = (request.keywords or {}).get(platform) or [request.topic]
            platform_root = run_root / platform
            platform_root.mkdir(parents=True, exist_ok=False)
            command = self._build_command(
                platform,
                request,
                platform_root,
                keywords,
            )
            try:
                return_code, stdout, stderr = await self._command_runner(
                    command,
                    self._config.root,
                    self._config.timeout_seconds,
                    cancel,
                    self._process_environment(platform),
                )
            except TypeError:
                return_code, stdout, stderr = await self._command_runner(
                    command,
                    self._config.root,
                    self._config.timeout_seconds,
                )
            if return_code != 0:
                detail = (stderr or stdout).strip()[-1500:]
                # INV-4：进程非零退出但已产出部分数据时，保留已采集内容
                # （MediaCrawler 常写完 JSONL 后异常退出），而不是丢弃整平台。
                partial = list(self._load_platform_posts(platform, platform_root))
                if partial:
                    logging.getLogger(__name__).warning(
                        "MediaCrawler %s exited %s but produced %d posts; "
                        "keeping partial data",
                        platform, return_code, len(partial),
                    )
                    platform_posts = partial
                else:
                    raise CrawlerExecutionError(
                        f"MediaCrawler failed for {platform} with exit code "
                        f"{return_code}: {detail or 'no diagnostic output'}"
                    )
            else:
                platform_posts = self._load_platform_posts(platform, platform_root)
            platform_posts = [
                post
                for post in platform_posts
                if _within_time_range(
                    post.get("published_at"),
                    request.time_range,
                )
            ]
            if platform == "weibo":
                platform_posts = [
                    post
                    for post in platform_posts
                    if not _is_weibo_marketing_noise(post)
                ]
            # Platform aggregate upstream cap：多关键词一次命令可能使上游
            # 抓取量成倍放大，Adapter 返回前按平台严格封顶（不依赖上游
            # crawler_max_notes_count 的 per-keyword/aggregate 语义）。
            upstream_limit = int(request.upstream_limit_per_platform or 0)
            if upstream_limit > 0 and len(platform_posts) > upstream_limit:
                platform_posts = sorted(
                    platform_posts,
                    key=lambda post: int(post.get("engagement") or 0),
                    reverse=True,
                )[:upstream_limit]
            posts.extend(platform_posts)

        # 按 native_id 去重（多组关键词可能命中同一批帖子）
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, object]] = []
        for post in posts:
            key = (
                str(post.get("platform") or ""),
                str(post.get("native_id") or post.get("id") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(post)
        if not deduped:
            detail = (stderr or stdout).strip()[-1500:]
            raise CrawlerExecutionError(
                f"MediaCrawler returned no content for {request.platforms}. "
                f"The platform login may have failed or the page structure may "
                f"have changed. Diagnostic output: {detail or 'none'}"
            )
        return deduped

    def _validate_installation(self) -> None:
        if not (self._config.root / "main.py").is_file():
            raise CrawlerConfigurationError(
                f"MediaCrawler main.py was not found under {self._config.root}"
            )
        if not self._config.python_executable.is_file():
            raise CrawlerConfigurationError(
                "MEDIACRAWLER_PYTHON_EXECUTABLE does not point to a Python executable"
            )
        if self._config.entrypoint is not None and not self._config.entrypoint.is_file():
            raise CrawlerConfigurationError(
                "MEDIACRAWLER_ENTRYPOINT does not point to a Python script"
            )
        if self._config.login_type not in {"qrcode", "phone", "cookie"}:
            raise CrawlerConfigurationError(
                "MEDIACRAWLER_LOGIN_TYPE must be qrcode, phone, or cookie"
            )
        if self._config.usage_mode != "research":
            raise CrawlerConfigurationError(
                "MediaCrawler is licensed only for non-commercial learning and research; "
                "MEDIACRAWLER_USAGE_MODE must remain 'research'"
            )

    def _validate_output_capacity(self) -> None:
        """Bound retained JSONL runs without deleting user data automatically."""
        root = self._config.output_root
        if not root.exists():
            return
        run_count = sum(1 for item in root.iterdir() if item.is_dir())
        if run_count >= max(self._config.max_output_runs, 1):
            raise CrawlerConfigurationError(
                f"MediaCrawler output retention limit ({self._config.max_output_runs}) "
                f"was reached under {root}. Review and remove explicit old run "
                "directories manually before collecting again."
            )

    def _process_environment(self, platform: str) -> dict[str, str]:
        cookies = {
            "weibo": self._config.weibo_cookies,
            "bilibili": self._config.bilibili_cookies,
            "tieba": self._config.tieba_cookies,
            "zhihu": self._config.zhihu_cookies,
            "douyin": self._config.douyin_cookies,
        }[platform]
        return {_COOKIE_ENV: cookies} if cookies else {}

    def _build_command(
        self,
        platform: str,
        request: CrawlRequest,
        output_root: Path,
        keywords: list[str] | None = None,
    ) -> list[str]:
        cookies = {
            "weibo": self._config.weibo_cookies,
            "bilibili": self._config.bilibili_cookies,
            "tieba": self._config.tieba_cookies,
            "zhihu": self._config.zhihu_cookies,
            "douyin": self._config.douyin_cookies,
        }[platform]
        if self._config.login_type == "cookie" and not cookies:
            raise CrawlerConfigurationError(
                f"Cookie login is enabled but no cookie is configured for {platform}"
            )

        # Discovery 传平台 aggregate 上限；legacy 调用保持 fetch_limit_for。
        upstream_limit = request.upstream_limit_per_platform
        if upstream_limit is None:
            upstream_limit = fetch_limit_for(request)
        # Discovery 必须真正关闭评论抓取（include_comments=false 时上游
        # 不进入评论采集逻辑）；legacy 调用回退适配器默认。
        include_comments = request.include_comments
        if include_comments is None:
            include_comments = self._config.include_comments

        command = [
            str(self._config.python_executable),
            str(self._config.entrypoint or "main.py"),
            "--platform",
            PLATFORM_CODES[platform],
            "--lt",
            self._config.login_type,
            "--type",
            "search",
            "--keywords",
            ",".join(keywords or [request.topic]),
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(output_root),
            "--crawler_max_notes_count",
            str(int(upstream_limit)),
            "--max_comments_count_singlenotes",
            str(
                min(
                    int(getattr(request, "comment_limit", 10)),
                    int(self._config.max_comments_per_post),
                )
            ),
            "--get_comment",
            str(bool(include_comments)).lower(),
            "--headless",
            str(self._config.headless).lower(),
            "--max_concurrency_num",
            "1",
        ]
        return command

    def _load_platform_posts(
        self,
        platform: str,
        output_root: Path,
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for raw in self._load_jsonl(output_root, "contents"):
            records.append(self._normalize(platform, raw))

        comments_by_post: dict[str, list[dict[str, object]]] = {}
        for raw_comment in self._load_jsonl(output_root, "comments"):
            comment = self._normalize_comment(platform, raw_comment)
            parent_content_id = str(comment.pop("_content_native_id"))
            comments_by_post.setdefault(parent_content_id, []).append(comment)
        for post in records:
            post["comments"] = comments_by_post.get(
                str(post.get("native_id") or ""),
                [],
            )
        return records

    @staticmethod
    def _load_jsonl(
        output_root: Path,
        item_type: str,
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for path in output_root.rglob(f"*{item_type}*.jsonl"):
            with path.open(encoding="utf-8") as source:
                for line_number, line in enumerate(source, start=1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise CrawlerExecutionError(
                            f"Invalid JSONL in {path.name} at line {line_number}"
                        ) from exc
                    if isinstance(raw, dict):
                        records.append(raw)
        return records

    @staticmethod
    def _normalize_comment(
        platform: str,
        raw: dict[str, object],
    ) -> dict[str, object]:
        content_field = {
            "weibo": "note_id",
            "bilibili": "video_id",
            "tieba": "note_id",
            "zhihu": "content_id",
            "douyin": "aweme_id",
        }[platform]
        native_id = str(
            raw.get("comment_id")
            or raw.get("id")
            or raw.get("cid")
            or uuid4()
        )
        parent_native_id = str(
            raw.get("parent_comment_id")
            or raw.get("reply_id")
            or raw.get("rootid")
            or ""
        )
        if parent_native_id in {"0", "None", "null"}:
            parent_native_id = ""
        metrics = {
            key: _parse_metric(raw.get(key))
            for key in (
                "like_count",
                "comment_like_count",
                "sub_comment_count",
            )
            if raw.get(key) is not None
        }
        return {
            "_content_native_id": str(raw.get(content_field) or ""),
            "native_id": native_id,
            "parent_native_id": parent_native_id or None,
            "content": str(
                raw.get("content")
                or raw.get("text")
                or raw.get("content_text")
                or ""
            ),
            "author_id": str(
                raw.get("creator_hash")
                or raw.get("user_id")
                or raw.get("uid")
                or ""
            ),
            "author_name": str(raw.get("nickname") or ""),
            "published_at": _normalize_timestamp(
                raw.get("create_time")
                or raw.get("publish_time")
                or raw.get("created_time")
            ),
            "metrics": metrics,
            "raw": raw,
        }

    @staticmethod
    def _normalize(platform: str, raw: dict[str, object]) -> dict[str, object]:
        metric_fields: tuple[str, ...]
        cover_url = ""
        direct_video_url = ""
        image_urls: list[str] = []
        if platform == "weibo":
            identifier = str(raw.get("note_id") or raw.get("id") or uuid4())
            content_type = "post"
            title = ""
            content = str(raw.get("content") or "")
            published_at = _normalize_timestamp(
                raw.get("create_date_time") or raw.get("create_time")
            )
            metric_fields = ("liked_count", "comments_count", "shared_count")
            url = str(raw.get("note_url") or "")
        elif platform == "bilibili":
            identifier = str(raw.get("video_id") or raw.get("id") or uuid4())
            content_type = "video"
            title = str(raw.get("title") or "")
            description = str(raw.get("desc") or "")
            content = "\n".join(part for part in (title, description) if part)
            published_at = _normalize_timestamp(raw.get("create_time"))
            metric_fields = (
                "liked_count",
                "video_comment",
                "video_share_count",
                "video_favorite_count",
                "video_coin_count",
                "video_danmaku",
                "video_play_count",
            )
            url = str(raw.get("video_url") or "")
            cover_url = str(raw.get("video_cover_url") or "")
            # MediaCrawler's `video_url` is the public Bilibili page, not a
            # playable media asset. Keep it as the source URL and expose only
            # an explicitly extracted CDN/download URL as video media.
            direct_video_url = str(raw.get("video_download_url") or "")
        elif platform == "tieba":
            identifier = str(raw.get("note_id") or raw.get("id") or uuid4())
            content_type = "thread"
            title = str(raw.get("title") or "")
            content = "\n".join(
                part for part in (title, str(raw.get("desc") or "")) if part
            )
            published_at = _normalize_timestamp(raw.get("publish_time"))
            metric_fields = ("total_replay_num",)
            url = str(raw.get("note_url") or "")
        elif platform == "zhihu":
            identifier = str(raw.get("content_id") or raw.get("id") or uuid4())
            content_type = str(raw.get("content_type") or "answer")
            title = str(raw.get("title") or "")
            content = "\n".join(
                part
                for part in (
                    title,
                    str(raw.get("content_text") or raw.get("desc") or ""),
                )
                if part
            )
            published_at = _normalize_timestamp(raw.get("created_time"))
            metric_fields = ("voteup_count", "comment_count")
            url = str(raw.get("content_url") or "")
        else:
            identifier = str(raw.get("aweme_id") or raw.get("id") or uuid4())
            raw_aweme_type = str(raw.get("aweme_type") or "")
            content_type = {
                "0": "video",
                "68": "image",
            }.get(raw_aweme_type, f"aweme_{raw_aweme_type}" if raw_aweme_type else "video")
            raw_title = str(raw.get("title") or "").strip()
            description = str(raw.get("desc") or "").strip()
            title = raw_title or description
            content_parts = []
            for part in (raw_title, description):
                if part and part not in content_parts:
                    content_parts.append(part)
            content = "\n".join(content_parts)
            published_at = _normalize_timestamp(raw.get("create_time"))
            metric_fields = (
                "liked_count",
                "comment_count",
                "share_count",
                "collected_count",
            )
            url = str(raw.get("aweme_url") or raw.get("video_url") or "")
            cover_url = str(raw.get("cover_url") or "")
            direct_video_url = str(raw.get("video_download_url") or "")
            note_download_url = str(raw.get("note_download_url") or "")
            image_urls = [
                item.strip()
                for item in note_download_url.split(",")
                if item.strip()
            ]

        metrics = {
            field: _parse_metric(raw.get(field))
            for field in metric_fields
        }
        engagement = sum(metrics.values())

        return {
            "id": f"{platform}-{identifier}",
            "native_id": identifier,
            "platform": platform,
            "content_type": content_type,
            "title": title,
            "author": str(
                raw.get("nickname")
                or raw.get("user_nickname")
                or raw.get("creator_nickname")
                or raw.get("creator_hash")
                or raw.get("user_id")
                or "unknown"
            ),
            "content": content,
            "published_at": published_at,
            "sentiment": "unknown",
            "engagement": engagement,
            "metrics": metrics,
            "url": url,
            "cover_url": cover_url,
            "video_url": direct_video_url,
            "image_urls": image_urls,
            "source_keyword": str(raw.get("source_keyword") or ""),
            "is_demo": False,
            "raw": raw,
        }


def _parse_metric(value: object) -> int:
    if value is None:
        return 0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    multipliers = {"万": 10_000, "亿": 100_000_000, "k": 1_000, "w": 10_000}
    suffix = text[-1].lower()
    multiplier = multipliers.get(suffix, 1)
    if multiplier != 1:
        text = text[:-1]
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0
    number = float(match.group())
    if not math.isfinite(number):
        return 0
    return max(0, int(number * multiplier))


def _normalize_timestamp(value: object) -> str:
    if value is None or value == "":
        return ""
    numeric_value: int | float | str | None = None
    if isinstance(value, (int, float)):
        numeric_value = value
    elif isinstance(value, str) and value.isdigit():
        numeric_value = value
    if numeric_value is not None:
        timestamp = float(numeric_value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CHINA_TIMEZONE)
    return parsed.astimezone(UTC).isoformat()


def _within_time_range(
    published_at: object,
    time_range: dict[str, str | None],
) -> bool:
    if not time_range:
        return True
    candidate = _parse_iso_datetime(published_at)
    start = _parse_time_bound(time_range.get("start"), end_of_day=False)
    end = _parse_time_bound(time_range.get("end"), end_of_day=True)
    if candidate is None:
        return start is None and end is None
    if start is not None and candidate < start:
        return False
    if end is not None and candidate > end:
        return False
    return True


def _parse_iso_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CHINA_TIMEZONE)
    return parsed.astimezone(UTC)


def _parse_time_bound(value: object, *, end_of_day: bool) -> datetime | None:
    if isinstance(value, str) and len(value.strip()) == 10:
        suffix = "T23:59:59.999999+08:00" if end_of_day else "T00:00:00+08:00"
        return _parse_iso_datetime(f"{value.strip()}{suffix}")
    return _parse_iso_datetime(value)
