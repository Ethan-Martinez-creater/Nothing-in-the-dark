from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.application.ports.crawler import CrawlRequest
from app.core.errors import CrawlerConfigurationError, CrawlerExecutionError
from app.infrastructure.crawler.mediacrawler import (
    MediaCrawlerAdapter,
    MediaCrawlerConfig,
    _TIMEOUT_EXIT_CODE,
    _normalize_timestamp,
    _parse_metric,
    _within_time_range,
)
from app.services.media_features import media_items_from_post


@pytest.fixture
def crawler_root(tmp_path: Path) -> Path:
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    (root / "main.py").write_text("# test entrypoint\n", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_adapter_runs_supported_platforms_and_normalizes_jsonl(
    tmp_path: Path,
    crawler_root: Path,
) -> None:
    commands: list[list[str]] = []

    async def fake_runner(
        command: Sequence[str],
        cwd: Path,
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        assert cwd == crawler_root
        assert timeout_seconds == 12
        command_list = list(command)
        commands.append(command_list)
        output = Path(command_list[command_list.index("--save_data_path") + 1])
        platform = command_list[command_list.index("--platform") + 1]
        folder = output / ("weibo" if platform == "wb" else "bili") / "jsonl"
        folder.mkdir(parents=True)
        raw = (
            {
                "note_id": "11",
                "content": "微博内容",
                "nickname": "微博用户",
                "create_time": 1_700_000_000_000,
                "liked_count": "1.2万",
                "comments_count": "8",
                "shared_count": 2,
                "note_url": "https://weibo.example/11",
            }
            if platform == "wb"
            else {
                "video_id": "BV1",
                "title": "视频标题",
                "desc": "视频说明",
                "nickname": "UP主",
                "create_time": 1_700_000_100,
                "liked_count": "2k",
                "video_comment": "20",
                "video_url": "https://bilibili.example/BV1",
                "video_cover_url": "https://i0.hdslb.com/bfs/archive/cover.jpg",
            }
        )
        (folder / "search_contents_20260101.jsonl").write_text(
            json.dumps(raw, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        comment = (
            {
                "comment_id": "wc-1",
                "note_id": "11",
                "content": "微博评论",
                "parent_comment_id": "",
                "nickname": "评论用户",
                "create_time": 1_700_000_200,
                "comment_like_count": 3,
            }
            if platform == "wb"
            else {
                "comment_id": "bc-1",
                "video_id": "BV1",
                "content": "B站评论",
                "parent_comment_id": "0",
                "nickname": "评论用户",
                "create_time": 1_700_000_200,
                "like_count": 4,
            }
        )
        (folder / "search_comments_20260101.jsonl").write_text(
            json.dumps(comment, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return 0, "ok", ""

    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
            timeout_seconds=12,
        ),
        command_runner=fake_runner,
    )
    posts = await adapter.collect(
        CrawlRequest(
            topic="测试主题",
            platforms=["weibo", "bilibili"],
            time_range={"start": None, "end": None},
            limit_per_platform=5,
        )
    )

    assert len(commands) == 2
    assert all(
        command[command.index("--crawler_max_notes_count") + 1] == "5"
        for command in commands
    )
    assert all(
        command[command.index("--max_comments_count_singlenotes") + 1] == "10"
        for command in commands
    )
    assert [post["platform"] for post in posts] == ["weibo", "bilibili"]
    assert posts[0]["id"] == "weibo-11"
    assert posts[0]["engagement"] == 12_010
    assert posts[0]["is_demo"] is False
    assert posts[1]["content"] == "视频标题\n视频说明"
    assert posts[1]["engagement"] == 2_020
    assert posts[1]["url"] == "https://bilibili.example/BV1"
    assert posts[1]["cover_url"] == "https://i0.hdslb.com/bfs/archive/cover.jpg"
    assert posts[1]["video_url"] == ""
    assert [item["media_type"] for item in media_items_from_post(posts[1])] == [
        "image"
    ]
    assert posts[0]["comments"][0]["native_id"] == "wc-1"
    assert posts[1]["comments"][0]["native_id"] == "bc-1"
    assert posts[1]["comments"][0]["parent_native_id"] is None


@pytest.mark.asyncio
async def test_cookie_mode_requires_platform_cookie(
    tmp_path: Path,
    crawler_root: Path,
) -> None:
    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
            login_type="cookie",
        )
    )

    with pytest.raises(CrawlerConfigurationError, match="no cookie"):
        await adapter.collect(
            CrawlRequest(
                topic="测试",
                platforms=["weibo"],
                time_range={"start": None, "end": None},
            )
        )


@pytest.mark.asyncio
async def test_adapter_rejects_successful_process_with_no_content(
    tmp_path: Path,
    crawler_root: Path,
) -> None:
    async def empty_runner(
        _command: Sequence[str],
        _cwd: Path,
        _timeout_seconds: float,
    ) -> tuple[int, str, str]:
        return 0, "login failed", ""

    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
        ),
        command_runner=empty_runner,
    )

    with pytest.raises(CrawlerExecutionError, match="returned no content"):
        await adapter.collect(
            CrawlRequest(
                topic="测试",
                platforms=["bilibili"],
                time_range={"start": None, "end": None},
            )
        )


def test_metric_and_timestamp_normalization() -> None:
    assert _parse_metric("3.5万") == 35_000
    assert _parse_metric("1.2亿") == 120_000_000
    assert _parse_metric("invalid") == 0
    assert _normalize_timestamp(1_700_000_000).startswith("2023-11")
    assert _normalize_timestamp(None) == ""
    assert not _within_time_range(
        "not-a-time",
        {"start": "2026-07-30", "end": "2026-07-30"},
    )
    # Date-only crawler bounds represent an Asia/Shanghai natural day.
    assert _within_time_range(
        "2026-07-30T15:59:59+00:00",
        {"start": "2026-07-30", "end": "2026-07-30"},
    )
    assert not _within_time_range(
        "2026-07-30T16:00:00+00:00",
        {"start": "2026-07-30", "end": "2026-07-30"},
    )


@pytest.mark.parametrize(
    ("platform", "raw", "expected_id", "expected_type"),
    [
        (
            "tieba",
            {
                "note_id": "tb-1",
                "title": "贴吧主题",
                "desc": "楼主内容",
                "publish_time": "2026-07-01T10:00:00+08:00",
                "total_replay_num": 12,
                "note_url": "https://tieba.baidu.com/p/1",
            },
            "tieba-tb-1",
            "thread",
        ),
        (
            "zhihu",
            {
                "content_id": "zh-1",
                "content_type": "answer",
                "title": "知乎问题",
                "content_text": "回答内容",
                "created_time": 1_700_000_000,
                "voteup_count": 20,
                "content_url": "https://www.zhihu.com/question/1/answer/1",
            },
            "zhihu-zh-1",
            "answer",
        ),
        (
            "douyin",
            {
                "aweme_id": "dy-1",
                "title": "抖音视频",
                "create_time": 1_700_000_000,
                "liked_count": "2万",
                "aweme_url": "https://www.douyin.com/video/1",
            },
            "douyin-dy-1",
            "video",
        ),
    ],
)
def test_new_platform_normalization(
    platform: str,
    raw: dict[str, object],
    expected_id: str,
    expected_type: str,
) -> None:
    normalized = MediaCrawlerAdapter._normalize(platform, raw)
    assert normalized["id"] == expected_id
    assert normalized["content_type"] == expected_type
    assert normalized["native_id"]
    assert normalized["url"]


@pytest.mark.asyncio
async def test_cookie_is_passed_via_child_environment_not_command_line(
    tmp_path: Path,
    crawler_root: Path,
) -> None:
    captured_command: list[str] = []
    captured_environment: dict[str, str] = {}

    async def runner(
        command, cwd, timeout_seconds, cancel_event, process_environment
    ):
        captured_command.extend(command)
        captured_environment.update(process_environment)
        output = Path(command[command.index("--save_data_path") + 1])
        folder = output / "weibo" / "jsonl"
        folder.mkdir(parents=True)
        (folder / "search_contents.jsonl").write_text(
            json.dumps(
                {
                    "note_id": "cookie-test",
                    "content": "这是一条用于验证 Cookie 安全传递的微博内容",
                    "create_time": 1_700_000_000,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0, "", ""

    secret = "session=super-secret"
    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
            login_type="cookie",
            weibo_cookies=secret,
        ),
        command_runner=runner,
    )
    await adapter.collect(CrawlRequest(topic="测试", platforms=["weibo"], time_range={}))

    assert secret not in captured_command
    assert "--cookies" not in captured_command
    assert captured_environment["COIFESP_MEDIACRAWLER_COOKIES"] == secret
    assert os.environ.get("COIFESP_MEDIACRAWLER_COOKIES") is None


def test_output_capacity_is_bounded_without_automatic_deletion(
    tmp_path: Path,
    crawler_root: Path,
) -> None:
    output_root = tmp_path / "runs"
    output_root.mkdir()
    retained = output_root / "existing-run"
    retained.mkdir()
    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=output_root,
            python_executable=Path(__file__),
            max_output_runs=1,
        )
    )

    with pytest.raises(CrawlerConfigurationError, match="retention limit"):
        adapter._validate_output_capacity()
    assert retained.is_dir()


def test_non_research_usage_is_rejected(
    tmp_path: Path,
    crawler_root: Path,
) -> None:
    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
            usage_mode="commercial",
        )
    )
    with pytest.raises(CrawlerConfigurationError, match="non-commercial"):
        adapter._validate_installation()


# ---------- 登录态感知 headless ----------


def _write_cookie_db(crawler_root: Path, platform_code: str, names: list[str]) -> None:
    """构造一个最小 Chrome Cookies SQLite（只读检测用）。"""
    import sqlite3

    db = (
        crawler_root
        / "browser_data"
        / f"{platform_code}_user_data_dir"
        / "Default"
        / "Network"
        / "Cookies"
    )
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cookies (name TEXT PRIMARY KEY, value TEXT, host_key TEXT)"
    )
    for name in names:
        conn.execute(
            "INSERT INTO cookies (name, value, host_key) VALUES (?, ?, ?)",
            (name, "v", "example.com"),
        )
    conn.commit()
    conn.close()


def _make_adapter(
    crawler_root: Path,
    tmp_path: Path,
    *,
    headless: bool = True,
    login_type: str = "qrcode",
    weibo_cookies: str = "",
) -> MediaCrawlerAdapter:
    return MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
            headless=headless,
            login_type=login_type,
            weibo_cookies=weibo_cookies,
        )
    )


def test_headless_keeps_background_when_platform_logged_in(
    tmp_path: Path, crawler_root: Path
) -> None:
    _write_cookie_db(crawler_root, "dy", ["sessionid", "sid_guard"])
    adapter = _make_adapter(crawler_root, tmp_path, headless=True)
    assert adapter._effective_headless("douyin") is True


def test_headless_forces_foreground_when_platform_not_logged_in(
    tmp_path: Path, crawler_root: Path
) -> None:
    # 目录存在但只有匿名 cookie（无登录标志）。
    _write_cookie_db(crawler_root, "dy", ["ttwid", "__ac_signature"])
    adapter = _make_adapter(crawler_root, tmp_path, headless=True)
    assert adapter._effective_headless("douyin") is False


def test_headless_forces_foreground_when_no_profile_dir(
    tmp_path: Path, crawler_root: Path
) -> None:
    adapter = _make_adapter(crawler_root, tmp_path, headless=True)
    assert adapter._effective_headless("douyin") is False


def test_headless_respects_explicit_foreground_config(
    tmp_path: Path, crawler_root: Path
) -> None:
    _write_cookie_db(crawler_root, "dy", ["sessionid"])
    adapter = _make_adapter(crawler_root, tmp_path, headless=False)
    assert adapter._effective_headless("douyin") is False


def test_headless_background_for_cookie_configured_platform(
    tmp_path: Path, crawler_root: Path
) -> None:
    # 未建 profile，但平台配置了 cookie → 无需可见扫码窗口，保持后台。
    adapter = _make_adapter(
        crawler_root, tmp_path, headless=True, weibo_cookies="session=secret"
    )
    assert adapter._effective_headless("weibo") is True


def test_headless_background_when_login_type_is_cookie(
    tmp_path: Path, crawler_root: Path
) -> None:
    adapter = _make_adapter(
        crawler_root, tmp_path, headless=True, login_type="cookie"
    )
    assert adapter._effective_headless("douyin") is True


def test_headless_falls_back_to_background_on_cookie_read_error(
    tmp_path: Path, crawler_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3 as _sqlite3

    _write_cookie_db(crawler_root, "dy", ["sessionid"])

    def _broken_connect(*args, **kwargs):  # noqa: ARG001
        raise OSError("locked")

    monkeypatch.setattr(_sqlite3, "connect", _broken_connect)
    adapter = _make_adapter(crawler_root, tmp_path, headless=True)
    # 读取失败保守按已登录处理（不强制切前台打扰用户）。
    assert adapter._effective_headless("douyin") is True


@pytest.mark.asyncio
async def test_collect_passes_login_aware_headless_to_command(
    tmp_path: Path, crawler_root: Path
) -> None:
    commands: list[list[str]] = []

    async def fake_runner(command, cwd, timeout_seconds):
        commands.append(list(command))
        output = Path(command[command.index("--save_data_path") + 1])
        folder = output / "weibo" / "jsonl"
        folder.mkdir(parents=True)
        (folder / "search_contents.jsonl").write_text(
            json.dumps(
                {
                    "note_id": "h1",
                    "content": "headless 测试",
                    "create_time": 1_700_000_000,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0, "", ""

    # 微博已登录（SUB）→ 后台；抖音无 profile → 前台。
    _write_cookie_db(crawler_root, "wb", ["SUB", "WBPSESS"])
    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
            headless=True,
        ),
        command_runner=fake_runner,
    )
    await adapter.collect(
        CrawlRequest(
            topic="测试",
            platforms=["weibo", "douyin"],
            time_range={"start": None, "end": None},
        )
    )
    assert len(commands) == 2
    by_platform = {
        cmd[cmd.index("--platform") + 1]: cmd[cmd.index("--headless") + 1]
        for cmd in commands
    }
    assert by_platform["wb"] == "true"  # 已登录 → 后台
    assert by_platform["dy"] == "false"  # 未登录 → 前台扫码


@pytest.mark.asyncio
async def test_collect_keeps_partial_data_on_timeout_exit(
    tmp_path: Path, crawler_root: Path
) -> None:
    # 回归：超时（_TIMEOUT_EXIT_CODE）时 collect 应保留已写出的 JSONL
    # 数据，而不是抛异常丢弃整平台（INV-4）。
    async def timeout_runner(command, cwd, timeout_seconds):
        output = Path(command[command.index("--save_data_path") + 1])
        folder = output / "weibo" / "jsonl"
        folder.mkdir(parents=True)
        (folder / "search_contents.jsonl").write_text(
            json.dumps(
                {
                    "note_id": "timeout-1",
                    "content": "超时前已采到的微博",
                    "create_time": 1_700_000_000,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return _TIMEOUT_EXIT_CODE, "", "MediaCrawler exceeded the timeout"

    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
        ),
        command_runner=timeout_runner,
    )
    posts = await adapter.collect(
        CrawlRequest(
            topic="测试",
            platforms=["weibo"],
            time_range={"start": None, "end": None},
        )
    )
    assert len(posts) == 1
    assert posts[0]["id"] == "weibo-timeout-1"


@pytest.mark.asyncio
async def test_collect_raises_when_timeout_and_no_partial_data(
    tmp_path: Path, crawler_root: Path
) -> None:
    async def timeout_runner(command, cwd, timeout_seconds):
        return _TIMEOUT_EXIT_CODE, "", "MediaCrawler exceeded the timeout"

    adapter = MediaCrawlerAdapter(
        MediaCrawlerConfig(
            root=crawler_root,
            output_root=tmp_path / "runs",
            python_executable=Path(__file__),
        ),
        command_runner=timeout_runner,
    )
    with pytest.raises(CrawlerExecutionError, match="exit code"):
        await adapter.collect(
            CrawlRequest(
                topic="测试",
                platforms=["weibo"],
                time_range={"start": None, "end": None},
            )
        )
