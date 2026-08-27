"""中文复杂语义与跨语言分析（11）。

统一语义管线（规则基线优先，LLM Provider 可选）：

1. TextNormalizer：可逆规范化 + span 映射（全半角 / URL@# 占位 / 重复压缩）。
2. LanguageDetector：段落级语言识别与混合比例。
3. LexiconResolver：版本化领域词典 / 别名 / 谐音候选（时间/平台/领域优先级）。
4. SemanticAnalyzer：情感 / 立场 / 反讽 / 主张片段 / 实体规则基线。
5. CrossLingualLinker：跨语言别名与语义近邻候选。
6. SemanticQualityGate：schema / 置信 / 冲突 / 回退；低置信输出 uncertain。

任何 LLM 输出必须经过 SemanticQualityGate 校验后才可落库；Provider
不可用时输出降级标记 fallback=true，现有流程继续可用。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# TextNormalizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NormalizedText:
    text: str
    # 归一化输出段 -> 原始输入区间（左闭右开）：
    # 每条为 (norm_start, norm_len, orig_start, orig_end)。占位符（URL/@/#）
    # 与重复压缩段记录整个 token/重复串的原始区间；普通字符 1:1 记录。
    span_map: list[tuple[int, int, int, int]] = field(default_factory=list)

    def orig_span(self, norm_start: int, norm_end: int) -> tuple[int, int]:
        """把归一化文本的 [norm_start, norm_end) 映射回原始字符区间。

        覆盖起始字符的段给出 orig 起点，覆盖末尾字符的段给出 orig 终点；
        普通/压缩段 1 字符，占位段 token 级映射。找不到覆盖段时回退
        norm 原样（防御性）。
        """
        if norm_start >= norm_end:
            return (norm_start, norm_start)

        def _segment_at(norm_index: int) -> tuple[int, int, int] | None:
            for ns, norm_len, os_, oe in self.span_map:
                if ns <= norm_index < ns + norm_len:
                    return (os_, oe)
            return None

        start_seg = _segment_at(norm_start)
        end_seg = _segment_at(norm_end - 1)
        if start_seg is None or end_seg is None:
            return (norm_start, norm_end)
        o_start = start_seg[0]
        # 覆盖末尾字符的段直接取原始终点（占位/压缩段覆盖整个原始区间）。
        o_end = end_seg[1]
        return (o_start, max(o_end, o_start + 1))


_PLACEHOLDER_TOKENS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://[^\s，。；！？\u4e00-\u9fff\uff00-\uffef]+"), "《URL》"),
    (re.compile(r"@[\w\u4e00-\u9fff_-]+"), "《AT》"),
    (re.compile(r"#[^\s#，。；！？]+#?"), "《HASH》"),
)


class TextNormalizer:
    """全半角 / Unicode 规范化，URL/@/# 占位，重复字符压缩，保留原文映射。

    实现按「原始区间」逐步扫描：每个占位 token 与每个重复压缩段记录
    (norm_start, orig_start, orig_end)，保证替换/压缩后的 span 能精确
    回溯到原文（不再因占位长度变化导致索引错位）。
    """

    _REPEAT_MIN = 3

    def normalize(self, text: str) -> NormalizedText:
        pieces: list[str] = []
        spans: list[tuple[int, int, int, int]] = []
        cursor = 0
        while cursor < len(text):
            best: tuple[re.Match[str], str] | None = None
            for pattern, placeholder in _PLACEHOLDER_TOKENS:
                match = pattern.search(text, cursor)
                if match and (best is None or match.start() < best[0].start()):
                    best = (match, placeholder)
            if best is None:
                self._append_plain(pieces, spans, text, cursor, len(text))
                break
            match, placeholder = best
            if match.start() > cursor:
                self._append_plain(pieces, spans, text, cursor, match.start())
            norm_start = len("".join(pieces))
            pieces.append(placeholder)
            spans.append(
                (norm_start, len(placeholder), match.start(), match.end())
            )
            cursor = match.end()
        return NormalizedText(text="".join(pieces), span_map=spans)

    def _append_plain(
        self,
        pieces: list[str],
        spans: list[tuple[int, int, int, int]],
        text: str,
        start: int,
        end: int,
    ) -> None:
        """NFKC 折叠 + 重复压缩；逐输出字符记录原始区间。"""
        folded = [
            unicodedata.normalize("NFKC", char) for char in text[start:end]
        ]
        i = 0
        n = len(folded)
        while i < n:
            j = i
            while j < n and folded[j] == folded[i]:
                j += 1
            count = j - i
            if count >= self._REPEAT_MIN:
                norm_start = len("".join(pieces))
                pieces.append(folded[i])
                spans.append((norm_start, 1, start + i, start + j))
            else:
                for k in range(i, j):
                    norm_start = len("".join(pieces))
                    pieces.append(folded[k])
                    spans.append(
                        (norm_start, 1, start + k, start + k + 1)
                    )
            i = j


# ---------------------------------------------------------------------------
# LanguageDetector
# ---------------------------------------------------------------------------


class LanguageDetector:
    """段落级语言识别：zh / en / ja / mixed，输出混合比例。"""

    _HAN = re.compile(r"[\u4e00-\u9fff]")
    _HIRAGANA = re.compile(r"[\u3040-\u309f]")
    _KATAKANA = re.compile(r"[\u30a0-\u30ff]")
    _LATIN = re.compile(r"[a-zA-Z]")

    def detect(self, text: str) -> dict[str, Any]:
        total = max(1, len(text.strip()))
        han = len(self._HAN.findall(text))
        hira = len(self._HIRAGANA.findall(text))
        kata = len(self._KATAKANA.findall(text))
        latin = len(self._LATIN.findall(text))
        japanese = hira + kata
        if han == 0 and japanese == 0 and latin == 0:
            return {"language": "unknown", "ratios": {}, "mixed": False}
        scores = {
            "zh": han,
            "ja": japanese,
            "en": latin,
        }
        ratios = {key: round(value / total, 3) for key, value in scores.items()}
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        top_lang, top_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0
        if top_score == 0:
            language = "unknown"
        elif second > 0 and second / max(top_score, 1) > 0.25:
            language = "mixed"
        else:
            language = top_lang
        return {"language": language, "ratios": ratios, "mixed": language == "mixed"}


# ---------------------------------------------------------------------------
# LexiconResolver
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LexiconEntry:
    term: str
    normalized: str = ""
    meaning: str = ""
    domain: str = "general"
    platform: str = ""
    language: str = "zh"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    review_state: str = "approved"

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "normalized": self.normalized,
            "meaning": self.meaning,
            "domain": self.domain,
            "platform": self.platform,
            "language": self.language,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "review_state": self.review_state,
        }


class LexiconResolver:
    """在给定词典条目集合上解析术语/别名/谐音候选。"""

    def resolve(
        self,
        text: str,
        entries: list[LexiconEntry],
        *,
        platform: str = "",
        domain: str = "",
        at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = at or datetime.now(UTC)
        lowered = text.lower()
        hits: list[dict[str, Any]] = []
        for entry in entries:
            if entry.review_state != "approved":
                continue
            if entry.valid_from and entry.valid_from > now:
                continue
            if entry.valid_to and entry.valid_to < now:
                continue
            if platform and entry.platform and entry.platform != platform:
                continue
            candidates = {entry.term.lower(), entry.normalized.lower()}
            candidates.discard("")
            for candidate in candidates:
                if candidate in lowered:
                    priority = 0
                    if entry.platform == platform:
                        priority += 2
                    if entry.domain == domain:
                        priority += 1
                    hits.append(
                        {
                            "term": entry.term,
                            "normalized": entry.normalized,
                            "meaning": entry.meaning,
                            "domain": entry.domain,
                            "platform": entry.platform,
                            "priority": priority,
                        }
                    )
                    break
        return sorted(hits, key=lambda item: item["priority"], reverse=True)


# ---------------------------------------------------------------------------
# SemanticAnalyzer（规则基线）
# ---------------------------------------------------------------------------

_NEGATION_WORDS = frozenset(
    {"不", "没", "无", "非", "莫", "别", "未", "休", "甭", "never", "not", "no"}
)
_TURN_WORDS = frozenset({"但是", "可是", "然而", "不过", "但", "却"})
_POSITIVE_WORDS = frozenset(
    {"好", "赞", "支持", "喜欢", "棒", "优秀", "满意", "精彩", "厉害", "牛", "爱",
     "great", "good", "like", "love"}
)
_NEGATIVE_WORDS = frozenset(
    {"差", "烂", "垃圾", "讨厌", "反对", "愤怒", "恶心", "失望", "糟糕", "坏", "坑",
     "骗", "bad", "awful", "hate"}
)
_IRONY_MARKERS = frozenset(
    {"呵呵", "哈哈", "真是", "太棒了", "了不起", "厉害了", "呵呵哒", "哦", "呵呵呵"}
)
_CLAIM_VERBS = frozenset(
    {"称", "表示", "宣布", "称称", "报道", "说", "声称", "强调",
     "指出", "证实", "否认", "回应"}
)
_ENTITY_MARKERS = ("机构", "公司", "大学", "部门", "组织", "局", "委", "集团", "平台")

_STANCE_POSITIVE = frozenset({"支持", "赞成", "力挺", "认同", "拥护", "挺"})
_STANCE_NEGATIVE = frozenset({"反对", "抵制", "谴责", "抗议", "拒绝", "不赞同", "不认可"})
_STANCE_NEUTRAL = frozenset({"中立", "观望", "存疑", "不清楚", "待定"})


@dataclass(slots=True)
class AnalysisResult:
    task: str
    label: str
    confidence: float
    provider: str = "rules"
    span: tuple[int, int] | None = None
    entity_ref: str | None = None
    uncertain: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "provider": self.provider,
            "span": list(self.span) if self.span else None,
            "entity_ref": self.entity_ref,
            "uncertain": self.uncertain,
            "detail": self.detail,
        }


class SemanticAnalyzer:
    """规则基线的结构化语义分析（情感/立场/反讽/主张/实体）。"""

    def analyze_sentiment(
        self, text: str, *, lexicon_hits: list[dict[str, Any]] | None = None
    ) -> AnalysisResult:
        normalized = unicodedata.normalize("NFKC", text)
        positive = self._count_polarity(normalized, _POSITIVE_WORDS)
        negative = self._count_polarity(normalized, _NEGATIVE_WORDS)
        # 词典命中携带情感标签时纳入计数。
        for hit in lexicon_hits or []:
            meaning = str(hit.get("meaning") or "")
            if any(word in meaning for word in _POSITIVE_WORDS):
                positive += 1
            if any(word in meaning for word in _NEGATIVE_WORDS):
                negative += 1
        total = positive + negative
        if total == 0:
            return AnalysisResult("sentiment", "neutral", 0.3, uncertain=True)
        if positive > negative:
            label = "positive"
        elif negative > positive:
            label = "negative"
        else:
            label = "neutral"
        confidence = 0.55 + 0.35 * (abs(positive - negative) / total)
        return AnalysisResult("sentiment", label, min(confidence, 0.95))

    @staticmethod
    def _count_polarity(text: str, words: frozenset[str]) -> int:
        """统计极性词数量，词前 3 字符内出现否定词则翻转该词极性。"""
        count = 0
        for word in words:
            start = 0
            while True:
                idx = text.find(word, start)
                if idx < 0:
                    break
                prefix = text[max(0, idx - 3) : idx]
                if not any(neg in prefix for neg in _NEGATION_WORDS):
                    count += 1
                start = idx + len(word)
        return count

    def analyze_stance(self, text: str) -> AnalysisResult:
        for word in _STANCE_POSITIVE:
            if word in text:
                return AnalysisResult("stance", "support", 0.8)
        for word in _STANCE_NEGATIVE:
            if word in text:
                return AnalysisResult("stance", "oppose", 0.8)
        for word in _STANCE_NEUTRAL:
            if word in text:
                return AnalysisResult("stance", "neutral", 0.6)
        return AnalysisResult("stance", "unknown", 0.3, uncertain=True)

    def analyze_irony(self, text: str) -> AnalysisResult:
        markers = sum(1 for marker in _IRONY_MARKERS if marker in text)
        has_positive = any(word in text for word in _POSITIVE_WORDS)
        has_negative = any(word in text for word in _NEGATIVE_WORDS)
        # 反讽信号：夸张语气词 + 情感反转 或 引号 + 正面词。
        quoted_praise = bool(
            re.search(r"[“\"『]\s*[^”\"』]*[好赞棒强厉害][^”\"』]*[”\"』]", text)
        )
        if markers >= 2 or (markers >= 1 and has_negative) or (quoted_praise and has_positive):
            return AnalysisResult("irony", "ironic", 0.72, detail={"markers": markers})
        if markers == 0:
            return AnalysisResult("irony", "not_ironic", 0.6)
        return AnalysisResult("irony", "uncertain", 0.4, uncertain=True)

    def analyze_claim_span(self, text: str) -> AnalysisResult:
        for verb in _CLAIM_VERBS:
            idx = text.find(verb)
            if idx >= 0:
                start = max(0, idx - 12)
                end = min(len(text), idx + len(verb) + 40)
                return AnalysisResult("claim_span", "claim", 0.68, span=(start, end))
        return AnalysisResult("claim_span", "no_claim", 0.5, uncertain=True)

    def analyze_entity(self, text: str) -> AnalysisResult:
        # 简版实体：机构/组织名词短语（连续汉字 + 机构标记）。
        pattern = re.compile(r"[\u4e00-\u9fff]{1,8}(?:" + "|".join(_ENTITY_MARKERS) + r")")
        match = pattern.search(text)
        if match:
            return AnalysisResult(
                "entity", "organization", 0.6,
                span=match.span(), entity_ref=match.group(),
            )
        return AnalysisResult("entity", "none", 0.4, uncertain=True)

    def analyze(
        self, task: str, text: str, *, lexicon_hits: list[dict[str, Any]] | None = None
    ) -> AnalysisResult:
        if task == "sentiment":
            return self.analyze_sentiment(text, lexicon_hits=lexicon_hits)
        if task == "stance":
            return self.analyze_stance(text)
        if task == "irony":
            return self.analyze_irony(text)
        if task == "claim_span":
            return self.analyze_claim_span(text)
        if task == "entity":
            return self.analyze_entity(text)
        raise ValueError(f"unknown semantic task {task!r}")


# ---------------------------------------------------------------------------
# CrossLingualLinker
# ---------------------------------------------------------------------------


class CrossLingualLinker:
    """跨语言实体别名与语义近邻候选（基于词典别名 + 字符重叠）。"""

    def link(
        self,
        text: str,
        entries: list[LexiconEntry],
        *,
        target_language: str = "en",
    ) -> list[dict[str, Any]]:
        lowered = text.lower()
        candidates: list[dict[str, Any]] = []
        for entry in entries:
            if entry.language == target_language or not entry.language:
                continue
            term_hit = entry.term.lower() in lowered
            norm_hit = bool(entry.normalized) and entry.normalized.lower() in lowered
            if term_hit or norm_hit:
                candidates.append(
                    {
                        "term": entry.term,
                        "language": entry.language,
                        "meaning": entry.meaning,
                        "domain": entry.domain,
                    }
                )
        return candidates[:10]


# ---------------------------------------------------------------------------
# SemanticQualityGate
# ---------------------------------------------------------------------------

_KNOWN_LABELS: dict[str, set[str]] = {
    "sentiment": {"positive", "negative", "neutral"},
    "stance": {"support", "oppose", "neutral", "unknown"},
    "irony": {"ironic", "not_ironic", "uncertain"},
    "claim_span": {"claim", "no_claim"},
    "entity": {"organization", "person", "location", "none"},
}


class SemanticQualityGate:
    """schema / 置信 / 冲突 / 回退策略。"""

    def validate(self, result: AnalysisResult) -> AnalysisResult:
        known = _KNOWN_LABELS.get(result.task)
        if known is not None and result.label not in known:
            return AnalysisResult(
                result.task, "uncertain", 0.0, provider=result.provider, uncertain=True
            )
        if result.confidence < 0.35:
            result.uncertain = True
        if result.uncertain:
            result.label = "uncertain"
        return result


# ---------------------------------------------------------------------------
# Provider 端口（LLM 结构化分类）
# ---------------------------------------------------------------------------


class SemanticProvider(Protocol):
    async def classify(
        self, task: str, text: str
    ) -> dict[str, Any] | None:
        """返回 {label, confidence} 或 None（不可用/失败）。"""


class LLMSemanticProvider:
    """LLM 结构化分类（可选）；失败返回 None 由规则基线兜底。"""

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def classify(self, task: str, text: str) -> dict[str, Any] | None:
        if self._llm is None or not getattr(self._llm, "configured", False):
            return None
        try:
            from app.infrastructure.llm import LLMMessage, ModelRoute

            prompt = (
                f"对以下中文社交文本做 {task} 分析，只输出 JSON："
                '{{"label": "...", "confidence": 0.0}}\n'
                f"文本：{text[:500]}"
            )
            response = await self._llm.complete(
                messages=[
                    LLMMessage(role="system", content="你是语义分析器，只输出 JSON。"),
                    LLMMessage(role="user", content=prompt),
                ],
                tools=[],
                route=ModelRoute.FAST,
            )
            content = (response.message.content or "").strip()
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                return None
            import json

            payload = json.loads(content[start : end + 1])
            label = str(payload.get("label") or "")
            confidence = float(payload.get("confidence") or 0.0)
            if not label:
                return None
            return {"label": label, "confidence": min(max(confidence, 0.0), 1.0)}
        except Exception:
            return None


# ---------------------------------------------------------------------------
# 汇总分析入口
# ---------------------------------------------------------------------------


async def analyze_text(
    text: str,
    tasks: list[str],
    *,
    lexicon: list[LexiconEntry] | None = None,
    platform: str = "",
    domain: str = "",
    llm: Any | None = None,
) -> dict[str, Any]:
    """统一语义分析：规范化 → 词典解析 → 任务分析（规则 + 可选 LLM）。

    返回结果带原文、规范化文本、每个任务的标签/置信/来源，以及降级标记。
    """
    normalizer = TextNormalizer()
    detector = LanguageDetector()
    resolver = LexiconResolver()
    analyzer = SemanticAnalyzer()
    gate = SemanticQualityGate()
    provider = LLMSemanticProvider(llm)

    normalized = normalizer.normalize(text)
    language = detector.detect(text)
    if lexicon:
        hits = resolver.resolve(
            text, lexicon, platform=platform, domain=domain
        )
    else:
        hits = []

    results: list[dict[str, Any]] = []
    for task in tasks:
        rule_result = analyzer.analyze(task, normalized.text, lexicon_hits=hits)
        if rule_result.uncertain:
            llm_result = await provider.classify(task, normalized.text)
            if llm_result is not None and llm_result.get("label"):
                candidate = AnalysisResult(
                    task,
                    str(llm_result["label"]),
                    float(llm_result.get("confidence") or 0.0),
                    provider="llm",
                )
                validated = gate.validate(candidate)
                better_than_rules = (
                    not validated.uncertain
                    and validated.confidence >= rule_result.confidence
                )
                if better_than_rules:
                    results.append(validated.to_dict())
                    continue
        validated_rule = gate.validate(rule_result)
        results.append(validated_rule.to_dict())

    return {
        "original": text,
        "normalized": normalized.text,
        "language": language,
        "lexicon_hits": hits,
        "results": results,
        "fallback": any(item["provider"] == "rules" for item in results),
        "semantic_version": "semantics-rules-1.0.0",
    }
