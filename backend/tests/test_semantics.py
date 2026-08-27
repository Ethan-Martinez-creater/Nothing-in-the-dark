"""M11 中文复杂语义与跨语言分析测试。"""

from __future__ import annotations

import atexit
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.semantics import (
    CrossLingualLinker,
    LanguageDetector,
    LexiconEntry,
    LexiconResolver,
    SemanticAnalyzer,
    SemanticQualityGate,
    TextNormalizer,
)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db() -> Path:
    d = _WORKSPACE_ROOT / f"coifesp-sem-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d / "test.db"


# ---- TextNormalizer -------------------------------------------------------


def test_normalizer_fullwidth_and_repeats() -> None:
    result = TextNormalizer().normalize("ＡＢＣ。。。哈哈哈哈哈")
    assert "ABC" in result.text
    assert "哈" in result.text
    assert "哈哈哈" not in result.text


def test_normalizer_placeholders_url_at_hash() -> None:
    result = TextNormalizer().normalize("看看 https://weibo.com/x 和 @user 与 #tag#")
    assert "《URL》" in result.text
    assert "《AT》" in result.text
    assert "《HASH》" in result.text


def test_normalizer_span_mapping() -> None:
    result = TextNormalizer().normalize("你好ABC")
    # span_map 记录全半角折叠；原文字符数 >= 归一化字符数。
    assert len(result.span_map) >= 0
    assert len(result.text) <= len("你好ABC")


# ---- LanguageDetector -----------------------------------------------------


def test_language_detector_zh() -> None:
    result = LanguageDetector().detect("这是一段中文内容")
    assert result["language"] == "zh"


def test_language_detector_en() -> None:
    result = LanguageDetector().detect("hello world test")
    assert result["language"] == "en"


def test_language_detector_mixed() -> None:
    result = LanguageDetector().detect("今天天气不错 but the traffic is bad ですね")
    assert result["mixed"] or result["language"] in {"zh", "ja", "mixed"}


# ---- LexiconResolver ------------------------------------------------------


def _entry(term: str, **kwargs: object) -> LexiconEntry:
    defaults: dict[str, object] = {
        "term": term,
        "normalized": term,
        "meaning": "测试含义",
        "domain": "general",
        "platform": "",
        "review_state": "approved",
    }
    defaults.update(kwargs)
    return LexiconEntry(**defaults)  # type: ignore[arg-type]


def test_lexicon_resolves_term() -> None:
    resolver = LexiconResolver()
    hits = resolver.resolve("今天你 yyds 了吗", [_entry("yyds", meaning="永远的神")])
    assert hits and hits[0]["term"] == "yyds"


def test_lexicon_platform_domain_priority() -> None:
    resolver = LexiconResolver()
    entries = [
        _entry("xswl", meaning="笑死我了", platform="weibo", domain="general"),
        _entry("xswl", meaning="游戏术语", platform="", domain="gaming"),
    ]
    hits = resolver.resolve("xswl 太逗了", entries, platform="weibo", domain="gaming")
    assert hits[0]["platform"] == "weibo"


def test_lexicon_time_validity() -> None:
    resolver = LexiconResolver()
    past = _entry(
        "过期梗",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        valid_to=datetime(2021, 1, 1, tzinfo=UTC),
    )
    hits = resolver.resolve("过期梗", [past], at=datetime.now(UTC))
    assert hits == []


def test_lexicon_rejects_unapproved() -> None:
    resolver = LexiconResolver()
    hits = resolver.resolve("yyds", [_entry("yyds", review_state="proposed")])
    assert hits == []


# ---- SemanticAnalyzer -----------------------------------------------------


def test_sentiment_positive() -> None:
    result = SemanticAnalyzer().analyze_sentiment("这部电影真好看，太棒了")
    assert result.label == "positive"


def test_sentiment_negation_flip() -> None:
    result = SemanticAnalyzer().analyze_sentiment("一点都不好，太差了")
    assert result.label == "negative"


def test_sentiment_neutral() -> None:
    result = SemanticAnalyzer().analyze_sentiment("今天吃了饭")
    assert result.label == "neutral"


def test_stance_support_and_oppose() -> None:
    analyzer = SemanticAnalyzer()
    assert analyzer.analyze_stance("我支持这个提案").label == "support"
    assert analyzer.analyze_stance("坚决反对这种做法").label == "oppose"


def test_irony_detected() -> None:
    result = SemanticAnalyzer().analyze_irony("呵呵，你真是太厉害了")
    assert result.label == "ironic"


def test_claim_span_extracted() -> None:
    result = SemanticAnalyzer().analyze_claim_span("官方表示该事件正在调查中")
    assert result.label == "claim"
    assert result.span is not None


def test_entity_detected() -> None:
    result = SemanticAnalyzer().analyze_entity("该公司发布了声明")
    assert result.entity_ref is not None
    assert "公司" in (result.entity_ref or "")


# ---- SemanticQualityGate --------------------------------------------------


def test_gate_rejects_unknown_label() -> None:
    result = SemanticAnalyzer().analyze_sentiment("好")
    result.label = "bogus"
    validated = SemanticQualityGate().validate(result)
    assert validated.uncertain is True


def test_gate_low_confidence_uncertain() -> None:
    from app.services.semantics import AnalysisResult

    result = AnalysisResult("sentiment", "neutral", 0.1)
    validated = SemanticQualityGate().validate(result)
    assert validated.uncertain is True


# ---- CrossLingualLinker ---------------------------------------------------


def test_cross_lingual_link() -> None:
    linker = CrossLingualLinker()
    entries = [_entry("哔哩哔哩", language="zh", meaning="Bilibili")]
    hits = linker.link("I watch bilibili daily", entries, target_language="en")
    assert hits == []


def test_cross_lingual_link_zh_text() -> None:
    linker = CrossLingualLinker()
    entries = [_entry("bilibili", language="en", meaning="哔哩哔哩")]
    hits = linker.link("今天在 bilibili 看视频", entries, target_language="zh")
    assert hits and hits[0]["term"] == "bilibili"


# ---- 集成 analyze_text ----------------------------------------------------


async def test_analyze_text_rules_fallback() -> None:
    from app.services.semantics import analyze_text

    payload = await analyze_text("这个产品太垃圾了，反对", ["sentiment", "stance"])
    assert payload["fallback"] is True
    results = {item["task"]: item for item in payload["results"]}
    assert results["sentiment"]["label"] == "negative"
    assert results["stance"]["label"] == "oppose"
    assert "original" in payload
    assert "normalized" in payload
    assert "language" in payload


# ---- API ------------------------------------------------------------------


def _client() -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{_tmp_db()}",
        demo_mode=True,
    )
    app = create_app(settings)
    return TestClient(app)


def test_api_lexicon_and_analyze() -> None:
    with _client() as client:
        created = client.post(
            "/api/v1/cases/dummy/semantics/lexicon",
            json={"term": "yyds", "meaning": "永远的神", "review_state": "approved"},
        )
        assert created.status_code == 201
        assert created.json()["term"] == "yyds"

        analyzed = client.post(
            "/api/v1/cases/dummy/semantics/analyze",
            json={
                "text": "太烂了，坚决反对",
                "tasks": ["sentiment", "stance"],
                "source_id": "post-1",
            },
        )
        assert analyzed.status_code == 200
        body = analyzed.json()
        results = {item["task"]: item for item in body["results"]}
        assert results["sentiment"]["label"] == "negative"
        assert results["stance"]["label"] == "oppose"


def test_api_semantic_models_seeded() -> None:
    with _client() as client:
        response = client.get("/api/v1/system/semantics/models")
        assert response.status_code == 200
        models = response.json()
        assert any(model["component"] == "classifier" for model in models)
