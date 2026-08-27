"""Sentiment and stance classification for Chinese social text.

Two layers:

* ``SentimentClassifier`` — deterministic keyword-dictionary classifier
  (positive/negative polarity words, negation reversal, intensifiers,
  stance phrases). No ML model, no network; used as an offline fallback.
* ``ModelSentimentClassifier`` — model-first wrapper: asks the isolated
  ML worker (Erlangshen-Roberta-110M-Sentiment, ``/v1/sentiment``) for the
  batch, and falls back to the dictionary when the worker is unavailable.

The experts classify every post/comment before the LLM interprets the
statistics, so the pipeline never blocks on the worker being down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.errors import ApplicationError

if TYPE_CHECKING:
    from app.infrastructure.sentiment.client import SentimentWorkerClient

# ---------------------------------------------------------------------------
# Lexicons (module-level constants; deterministic and introspectable)
# ---------------------------------------------------------------------------

_NEGATIVE_WORDS: frozenset[str] = frozenset(
    {
        # fraud / misinformation
        "虚假",
        "造谣",
        "炒作",
        "骗局",
        "诈骗",
        "骗人",
        "忽悠",
        "套路",
        "割韭菜",
        "夸大宣传",
        "虚假宣传",
        "误导",
        "欺骗",
        "瞒报",
        "谎报",
        "捏造",
        "篡改",
        "伪造",
        "冒用",
        "盗用",
        "抄袭",
        "剽窃",
        "水军",
        "刷单",
        "刷屏",
        "暗箱操作",
        "内幕",
        "舞弊",
        "作假",
        # scandals / misconduct
        "丑闻",
        "黑幕",
        "腐败",
        "隐瞒",
        "推诿",
        "不作为",
        "乱象",
        "敷衍",
        "搪塞",
        "狡辩",
        "甩锅",
        "双标",
        "装死",
        "不了了之",
        "投诉无门",
        "无人负责",
        "滥用",
        "侵权",
        "泄露",
        "隐私",
        # product / service failures
        "坑人",
        "差评",
        "劣质",
        "偷工减料",
        "以次充好",
        "掺假",
        "变质",
        "召回",
        "停产",
        "事故",
        "伤亡",
        "致死",
        "中毒",
        "过敏",
        "不良反应",
        "副作用",
        # business distress
        "滞销",
        "亏损",
        "破产",
        "裁员",
        "降薪",
        "拖欠",
        "赖账",
        "跑路",
        "失联",
        "爆雷",
        "崩盘",
        "暴跌",
        # negative emotion / attitude
        "恶意",
        "愤怒",
        "气愤",
        "失望",
        "糟心",
        "恶心",
        "痛心",
        "担忧",
        "焦虑",
        "恐慌",
        "恐惧",
        "害怕",
        "危险",
        "威胁",
        "垃圾",
        "废物",
        "恶臭",
        # hostility
        "抵制",
        "谴责",
        "抗议",
        "投诉",
    }
)

_POSITIVE_WORDS: frozenset[str] = frozenset(
    {
        # approval
        "点赞",
        "好评",
        "支持",
        "赞同",
        "认可",
        "肯定",
        "满意",
        "放心",
        "信任",
        "信赖",
        "靠谱",
        "可靠",
        "良心",
        "业界良心",
        "正能量",
        "优质",
        "出色",
        "优秀",
        "精湛",
        "一流",
        "完美",
        "惊喜",
        "感动",
        "振奋",
        "暖心",
        "给力",
        "好样的",
        "棒",
        "很棒",
        "太棒了",
        "不错",
        "不赖",
        "令人满意",
        "效果显著",
        "成绩斐然",
        "口碑好",
        "好评如潮",
        "五星",
        "满分",
        "赞不绝口",
        "值得",
        "值得推荐",
        "值得信赖",
        "推荐",
        # attributes
        "真实",
        "透明",
        "及时",
        "高效",
        "专业",
        "安全",
        "喜爱",
        "热爱",
        "感谢",
        "感激",
        "赞扬",
        "表扬",
        "敬佩",
        "羡慕",
        "喜欢",
        "放心使用",
    }
)

# Negation words that flip the polarity of a nearby sentiment word.
_NEGATION_WORDS: frozenset[str] = frozenset(
    {
        "不",
        "没",
        "无",
        "非",
        "别",
        "未",
        "莫",
        "勿",
        "没有",
        "不是",
        "并非",
        "不算",
        "不会",
        "不可能",
        "并未",
        "毫无",
        "毫不",
        "并不",
        "从不",
        "压根没",
    }
)

# Intensifiers amplify the polarity of a nearby sentiment word (max 2x).
_INTENSIFIER_WORDS: frozenset[str] = frozenset(
    {
        "非常",
        "极其",
        "特别",
        "太",
        "极度",
        "严重",
        "根本",
        "完全",
        "十分",
        "很",
        "超",
        "尤其",
        "超级",
        "万分",
        "彻底",
    }
)

_SUPPORTIVE_PHRASES: frozenset[str] = frozenset(
    {
        "支持",
        "赞同",
        "赞成",
        "力挺",
        "顶一个",
        "挺你",
        "支持楼主",
        "说得好",
        "没错",
        "同意",
        "看好",
        "点赞",
        "支持一下",
        "值得推荐",
        "值得信赖",
    }
)

_OPPOSING_PHRASES: frozenset[str] = frozenset(
    {
        "反对",
        "抵制",
        "谴责",
        "抗议",
        "愤怒",
        "失望",
        "投诉",
        "坚决反对",
        "不能接受",
        "无法接受",
        "强烈谴责",
        "不认同",
        "不赞成",
    }
)

_QUESTIONING_PHRASES: frozenset[str] = frozenset(
    {
        "质疑",
        "疑问",
        "真的吗",
        "靠谱吗",
        "为什么",
        "是不是",
        "怀疑",
        "存疑",
        "有待考证",
        "怎么证明",
        "依据是什么",
        "什么依据",
        "求真相",
    }
)

_LOOKBEHIND_CHARS = 4
_INTENSIFIER_MULTIPLIER = 1.5
_NEGATION_WINDOW = _LOOKBEHIND_CHARS

# Phrases that contain a negation character but do NOT negate polarity:
# "非常满意" (intensifier + positive), "特别满意", "没错，值得推荐".
# Without this, the single character "非" inside "非常" would flip the
# sentiment of every intensified word.
_NEGATION_EXCEPTION_PHRASES: frozenset[str] = frozenset(
    {"非常", "特别", "没错"}
)


def _find_offsets(text: str, word: str) -> list[int]:
    """Return every start offset of ``word`` inside ``text``."""
    offsets: list[int] = []
    start = 0
    while True:
        index = text.find(word, start)
        if index < 0:
            break
        offsets.append(index)
        start = index + len(word)
    return offsets


class SentimentClassifier:
    """Keyword-dictionary sentiment classifier.

    ``classify`` returns ``(label, score)`` where label is
    ``positive | neutral | negative`` and score ranges from -1 (strongly
    negative) to +1 (strongly positive). Polarity is deterministic and
    explainable through ``matched_words``.
    """

    @staticmethod
    def classify(text: str) -> tuple[str, float, list[str]]:
        """Return ``(label, score, matched_words)`` for one text."""
        positive = 0.0
        negative = 0.0
        matched: list[str] = []
        for word in _POSITIVE_WORDS:
            for offset in _find_offsets(text, word):
                weight = SentimentClassifier._word_weight(text, offset)
                if SentimentClassifier._negated(text, offset):
                    negative += weight
                else:
                    positive += weight
                matched.append(word)
        for word in _NEGATIVE_WORDS:
            for offset in _find_offsets(text, word):
                weight = SentimentClassifier._word_weight(text, offset)
                if SentimentClassifier._negated(text, offset):
                    positive += weight
                else:
                    negative += weight
                matched.append(word)
        # Un-normalised delta keeps intensifier differences visible; the
        # absolute value is not bounded to [-1, 1].
        score = round(positive - negative, 3)
        if score > 0.05:
            label = "positive"
        elif score < -0.05:
            label = "negative"
        else:
            label = "neutral"
        return label, score, matched

    @staticmethod
    def classify_batch(
        texts: list[str],
    ) -> list[tuple[str, float, list[str]]]:
        return [SentimentClassifier.classify(text) for text in texts]

    @staticmethod
    def _negated(text: str, offset: int) -> bool:
        """True when a negation word appears just before this offset.

        A negation hit inside an exception phrase (e.g. "非" inside "非常")
        is ignored so intensified positive texts are not flipped.
        """
        window = text[max(0, offset - _NEGATION_WINDOW) : offset]
        for word in _NEGATION_WORDS:
            if word not in window:
                continue
            if any(
                word in phrase and phrase in window
                for phrase in _NEGATION_EXCEPTION_PHRASES
            ):
                continue
            return True
        return False

    @staticmethod
    def _word_weight(text: str, offset: int) -> float:
        """Amplify polarity when an intensifier appears just before the word."""
        window = text[max(0, offset - _LOOKBEHIND_CHARS) : offset]
        if any(word in window for word in _INTENSIFIER_WORDS):
            return _INTENSIFIER_MULTIPLIER
        return 1.0


class StanceClassifier:
    """Keyword-dictionary stance classifier.

    Labels: ``supportive | opposing | questioning | neutral``.
    """

    @staticmethod
    def classify(text: str) -> str:
        if any(word in text for word in _SUPPORTIVE_PHRASES):
            return "supportive"
        if any(word in text for word in _OPPOSING_PHRASES):
            return "opposing"
        if any(word in text for word in _QUESTIONING_PHRASES):
            return "questioning"
        return "neutral"


def classify_text(text: str) -> dict[str, Any]:
    """Convenience entry point returning a serialisable classification."""
    label, score, matched = SentimentClassifier.classify(text)
    return {
        "sentiment": label,
        "score": score,
        "stance": StanceClassifier.classify(text),
        "matched_words": matched,
    }


# ---------------------------------------------------------------------------
# Model-first classification (Erlangshen worker + dictionary fallback)
# ---------------------------------------------------------------------------

# The worker model is binary (positive/negative); texts whose polarity score
# falls inside this band are labelled neutral instead of forcing a verdict.
_MODEL_NEUTRAL_THRESHOLD = 0.15


@dataclass(frozen=True, slots=True)
class ModelClassification:
    """One classified text: model result or dictionary fallback."""

    sentiment: str
    score: float
    confidence: float
    stance: str
    matched_words: list[str] = field(default_factory=list)
    source: str = "model"  # "model" | "dictionary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentiment": self.sentiment,
            "score": self.score,
            "confidence": self.confidence,
            "stance": self.stance,
            "matched_words": self.matched_words,
            "source": self.source,
        }


class ModelSentimentClassifier:
    """Model-first sentiment classification with a deterministic fallback.

    Production batches go to the isolated ML worker; when the worker is not
    configured or unreachable, every text is classified by the dictionary
    so analysis never blocks on infrastructure.
    """

    def __init__(self, worker: SentimentWorkerClient | None = None) -> None:
        self._worker = worker

    @property
    def configured(self) -> bool:
        return self._worker is not None and self._worker.configured

    async def classify_batch(
        self,
        texts: list[str],
    ) -> list[ModelClassification]:
        if not self.configured:
            return [self._dictionary_classification(text) for text in texts]
        try:
            results = await self._worker.classify(texts)
        except ApplicationError:
            results = None
        if results is None or len(results) != len(texts):
            return [self._dictionary_classification(text) for text in texts]
        return [
            self._merge_model_result(text, item)
            for text, item in zip(texts, results, strict=True)
        ]

    async def classify_one(self, text: str) -> ModelClassification:
        return (await self.classify_batch([text]))[0]

    def _merge_model_result(
        self,
        text: str,
        item: dict[str, Any],
    ) -> ModelClassification:
        score = float(item.get("score") or 0.0)
        if score > _MODEL_NEUTRAL_THRESHOLD:
            sentiment = "positive"
        elif score < -_MODEL_NEUTRAL_THRESHOLD:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        return ModelClassification(
            sentiment=sentiment,
            score=score,
            confidence=float(item.get("confidence") or 0.0),
            stance=StanceClassifier.classify(text),
            matched_words=[],
            source="model",
        )

    def _dictionary_classification(self, text: str) -> ModelClassification:
        return self.classify_dictionary(text)

    @staticmethod
    def classify_dictionary(text: str) -> ModelClassification:
        """Deterministic dictionary classification, usable without a worker."""
        label, score, matched = SentimentClassifier.classify(text)
        return ModelClassification(
            sentiment=label,
            score=score,
            confidence=abs(score),
            stance=StanceClassifier.classify(text),
            matched_words=matched,
            source="dictionary",
        )
