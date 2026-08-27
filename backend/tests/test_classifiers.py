"""Deterministic keyword classifiers: sentiment and stance."""

from app.services.classifiers import (
    SentimentClassifier,
    StanceClassifier,
    classify_text,
)


def test_positive_text_classifies_correctly() -> None:
    label, score, matched = SentimentClassifier.classify("这个产品非常可靠，值得推荐")
    assert label == "positive"
    assert score > 0
    assert "可靠" in matched
    assert "值得推荐" in matched


def test_negative_text_classifies_correctly() -> None:
    label, score, matched = SentimentClassifier.classify("太垃圾了，完全虚假宣传")
    assert label == "negative"
    assert score < 0
    assert "垃圾" in matched
    assert "虚假宣传" in matched


def test_neutral_text_classifies_correctly() -> None:
    label, score, _ = SentimentClassifier.classify("今天讨论了会议议程和时间安排")
    assert label == "neutral"
    assert score == 0.0


def test_negation_flips_polarity() -> None:
    label, _, _ = SentimentClassifier.classify("这家店并没有骗人，很良心")
    assert label == "positive"
    label2, _, _ = SentimentClassifier.classify("处置结果并不令人满意")
    assert label2 == "negative"


def test_intensifier_amplifies_score() -> None:
    _, strong_score, _ = SentimentClassifier.classify("非常满意，十分可靠")
    _, plain_score, _ = SentimentClassifier.classify("满意，可靠")
    assert strong_score > plain_score


def test_empty_text_returns_neutral() -> None:
    label, score, matched = SentimentClassifier.classify("")
    assert label == "neutral"
    assert score == 0.0
    assert matched == []


def test_batch_matches_individual_calls() -> None:
    texts = ["值得推荐", "太垃圾了", "今天下雨"]
    batch = SentimentClassifier.classify_batch(texts)
    for text, (label, score, matched) in zip(texts, batch, strict=True):
        individual = SentimentClassifier.classify(text)
        assert (label, score, matched) == individual


def test_stance_supportive() -> None:
    assert StanceClassifier.classify("我支持这个方案，点赞") == "supportive"


def test_stance_opposing() -> None:
    assert StanceClassifier.classify("我坚决反对这个政策") == "opposing"


def test_stance_questioning() -> None:
    assert StanceClassifier.classify("这个数据是真的吗？存疑") == "questioning"


def test_stance_neutral() -> None:
    assert StanceClassifier.classify("今天开会讨论了方案") == "neutral"


def test_classify_text_serialisable_shape() -> None:
    result = classify_text("非常好用，值得推荐")
    assert result["sentiment"] == "positive"
    assert result["stance"] == "supportive"
    assert isinstance(result["score"], float)
    assert isinstance(result["matched_words"], list)


def test_mixed_social_media_samples() -> None:
    """Hand-labelled samples: the classifier must not contradict the label."""
    samples = [
        ("官方回应及时，处理透明，值得点赞", "positive"),
        ("客服推诿，投诉无门，太失望了", "negative"),
        ("事件发生三小时后发布了通报", "neutral"),
        ("强烈谴责这种行为，不能接受", "negative"),
        ("大家支持一下，正能量满满", "positive"),
    ]
    for text, expected in samples:
        label, _, _ = SentimentClassifier.classify(text)
        assert label == expected, f"{text!r} -> {label}, expected {expected}"
