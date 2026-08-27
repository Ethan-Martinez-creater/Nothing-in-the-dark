"""Uncertainty, sampling bias & alternative explanations (08).

统一不确定性表达的确定性核心：保守置信组合、证据独立性分组、报告用语
约束、敏感性差异。等级 high/medium/low/insufficient；关键维度 insufficient
时整体不得高于 low；未校准分数不得作为百分比概率展示。
"""

from __future__ import annotations

from typing import Any

LEVELS = ("insufficient", "low", "medium", "high")
_LEVEL_RANK = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}

# 关键维度：覆盖不足或证据不足时，整体置信度上限为 low。
KEY_DIMENSIONS = ("coverage", "evidence_strength")

# 强断言用语（低/不足置信下禁止）。
STRONG_CLAIMS = ("证实", "确定", "事实表明", "确凿", "证明", "必然", "毋庸置疑")

QUALITY_DIMENSIONS = (
    "coverage",
    "sampling_bias",
    "measurement_uncertainty",
    "model_uncertainty",
    "evidence_strength",
    "robustness",
    "alternative_explanations",
)


def combine_confidence(dimensions: dict[str, str]) -> tuple[str, list[str]]:
    """保守组合各维度等级，返回 (final_level, forbidden_reasons)。

    关键维度为 insufficient 时整体不得高于 low；否则取各维度最低等级。
    """
    reasons: list[str] = []
    valid = {d: v for d, v in dimensions.items() if v in _LEVEL_RANK}
    if not valid:
        return "insufficient", ["no_dimensions"]

    base = min(valid.values(), key=lambda level: _LEVEL_RANK[level])

    for key_dim in KEY_DIMENSIONS:
        if dimensions.get(key_dim) == "insufficient":
            reasons.append(f"{key_dim}_insufficient")

    if reasons and _LEVEL_RANK[base] > _LEVEL_RANK["low"]:
        base = "low"

    return base, reasons


def group_evidence_by_source(evidence: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按原始来源分组证据：同源转载不重复增信，独立组数才是独立证据数。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        source = (
            item.get("source_url")
            or item.get("original_url")
            or item.get("source_id")
            or item.get("id")
            or str(id(item))
        )
        groups.setdefault(str(source), []).append(item)
    return list(groups.values())


def independent_evidence_count(evidence: list[dict[str, Any]]) -> int:
    return len(group_evidence_by_source(evidence))


def wording_for_level(level: str) -> dict[str, list[str]]:
    """报告用语映射：低/不足置信禁止强断言。"""
    if level == "high":
        return {"allowed": ["证实", "表明", "支持", "确定"], "forbidden": []}
    if level == "medium":
        return {"allowed": ["表明", "支持", "倾向于"], "forbidden": list(STRONG_CLAIMS)}
    if level == "low":
        return {"allowed": ["可能", "初步", "有限证据"], "forbidden": list(STRONG_CLAIMS)}
    return {"allowed": ["无法判断", "证据不足"], "forbidden": list(STRONG_CLAIMS)}


def assert_no_strong_claim(text: str, level: str) -> list[str]:
    """返回文本中与等级不符的强断言用语（空列表表示合规）。"""
    if level == "high":
        return []
    forbidden = wording_for_level(level)["forbidden"]
    return [word for word in forbidden if word in text]


def format_score(score: float, calibrated: bool) -> str:
    """未校准分数不显示为百分比概率，标记为 uncalibrated_score。"""
    if not calibrated:
        return f"uncalibrated_score={score:.3f}"
    return f"{score:.2f}"


def sensitivity_difference(
    baseline: dict[str, Any], variant: dict[str, Any]
) -> dict[str, Any]:
    """计算基线 vs 变体的输出差异（数值键给 delta，其余标 changed）。"""
    diff: dict[str, Any] = {}
    for key in set(baseline) | set(variant):
        base = baseline.get(key)
        var = variant.get(key)
        if isinstance(base, (int, float)) and isinstance(var, (int, float)):
            delta = var - base
            if delta == 0:
                continue
            diff[key] = {"baseline": base, "variant": var, "delta": delta}
        elif base != var:
            diff[key] = {"baseline": base, "variant": var, "changed": True}
    return diff


def is_conclusion_stable(diffs: list[dict[str, Any]], threshold: float = 0.2) -> bool:
    """结论是否在敏感性变体下稳健：所有数值 delta 的归一化幅度 <= threshold。"""
    for diff in diffs:
        for value in diff.values():
            if isinstance(value, dict) and "delta" in value and "baseline" in value:
                base = value["baseline"]
                if isinstance(base, (int, float)) and base != 0:
                    if abs(value["delta"] / base) > threshold:
                        return False
    return True
