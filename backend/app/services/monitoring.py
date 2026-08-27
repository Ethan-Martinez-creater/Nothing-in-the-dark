"""Continuous monitoring domain logic (01).

纯标准库实现，便于单元测试：

- Cron 风格调度下一触发时间计算（5 字段：分 时 日 月 周）。
- 时间窗规划：首次回溯、正常增量、重叠窗口、迟到数据水位线。
- 五类确定性告警规则：绝对量 / 增长率 / 鲁棒异常(MAD) / 关键账号 / 新叙事。

所有时间以 UTC 存储、以 Asia/Shanghai 等指定时区解释 cron 自然日边界；
本模块只做确定性计算，不调用任何 LLM。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"

# ---- cron 解析 -----------------------------------------------------------

_CRON_RANGES: dict[int, tuple[int, int]] = {
    0: (0, 59),  # minute
    1: (0, 23),  # hour
    2: (1, 31),  # day of month
    3: (1, 12),  # month
    4: (0, 6),   # day of week (0=Sunday)
}


def _parse_cron_field(field: str, index: int) -> set[int]:
    low, high = _CRON_RANGES[index]
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty cron field segment in '{field}'")
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            if not step_str.isdigit() or int(step_str) <= 0:
                raise ValueError(f"invalid cron step in '{field}'")
            step = int(step_str)
            part = base
        if part == "*" or part == "":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                raise ValueError(f"invalid cron range in '{field}'")
            start, end = int(a), int(b)
        else:
            if not part.isdigit():
                raise ValueError(f"invalid cron value in '{field}'")
            start = end = int(part)
        if start < low or end > high or start > end:
            raise ValueError(f"cron field '{field}' out of range [{low},{high}]")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(cron: str) -> list[set[int]]:
    fields = [f for f in cron.split() if f]
    if len(fields) != 5:
        raise ValueError("cron expression must have 5 fields (minute hour day month weekday)")
    return [_parse_cron_field(f, i) for i, f in enumerate(fields)]


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def _dom_allows(year: int, month: int, day: int, allowed: set[int]) -> bool:
    return day in allowed or (max(allowed) > 28 and day == _last_day_of_month(year, month))


def cron_next(
    cron: str,
    *,
    after: datetime,
    tz_name: str = DEFAULT_TIMEZONE,
    max_years: int = 5,
) -> datetime:
    minutes, hours, doms, months, dows = parse_cron(cron)
    tz = ZoneInfo(tz_name)
    if after.tzinfo is None:
        after = after.replace(tzinfo=tz)
    local = after.astimezone(tz)
    candidate = local.replace(second=0, microsecond=0) + timedelta(minutes=1)
    horizon = local + timedelta(days=365 * max_years)
    while candidate <= horizon:
        if (
            candidate.month in months
            and candidate.minute in minutes
            and candidate.hour in hours
            and (candidate.weekday() + 1) % 7 in dows
            and _dom_allows(candidate.year, candidate.month, candidate.day, doms)
        ):
            return candidate.astimezone(UTC)
        candidate += timedelta(minutes=1)
    raise ValueError(f"no cron match within {max_years} years for '{cron}'")


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def cooldown_bucket(at: datetime, cooldown_seconds: int) -> str:
    if cooldown_seconds <= 0:
        return "all"
    epoch = int(at.timestamp())
    return str(epoch // cooldown_seconds)


# ---- 时间窗规划 -----------------------------------------------------------

def compute_window(
    *,
    schedule_type: str,
    interval_seconds: int | None,
    cron: str | None,
    timezone: str,
    lookback_seconds: int,
    last_window_end: datetime | None,
    now: datetime,
    overlap_seconds: int = 0,
) -> tuple[datetime, datetime, bool]:
    now = to_utc(now)
    if last_window_end is None:
        return now - timedelta(seconds=lookback_seconds), now, True
    last = to_utc(last_window_end)
    start = last - timedelta(seconds=overlap_seconds)
    return start, now, False


def compute_next_scheduled_at(
    *,
    schedule_type: str,
    interval_seconds: int | None,
    cron: str | None,
    timezone: str,
    last_scheduled_at: datetime | None,
    now: datetime,
) -> datetime:
    now = to_utc(now)
    if schedule_type == "cron":
        if not cron:
            raise ValueError("cron schedule requires a cron expression")
        after = to_utc(last_scheduled_at) if last_scheduled_at else now
        return cron_next(cron, after=after, tz_name=timezone)
    if not interval_seconds or interval_seconds <= 0:
        raise ValueError("interval schedule requires a positive interval_seconds")
    if last_scheduled_at is None:
        return now
    nxt = to_utc(last_scheduled_at) + timedelta(seconds=interval_seconds)
    while nxt < now:
        nxt += timedelta(seconds=interval_seconds)
    return nxt


# ---- 告警规则 -------------------------------------------------------------

class AlertHit:
    def __init__(
        self,
        *,
        rule_type: str,
        severity: str,
        fingerprint: str,
        explanation: str,
        metric_snapshot: dict[str, Any],
        evidence_refs: dict[str, Any],
    ) -> None:
        self.rule_type = rule_type
        self.severity = severity
        self.fingerprint = fingerprint
        self.explanation = explanation
        self.metric_snapshot = metric_snapshot
        self.evidence_refs = evidence_refs

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "severity": self.severity,
            "fingerprint": self.fingerprint,
            "explanation": self.explanation,
            "metric_snapshot": self.metric_snapshot,
            "evidence_refs": self.evidence_refs,
        }


def _stable_fingerprint(*parts: object) -> str:
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _metric_value(window: dict[str, Any], metric: str) -> float:
    value = window.get(metric, 0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mad(values: list[float]) -> float:
    if not values:
        return 0.0
    m = median(values)
    return median(abs(v - m) for v in values)


def evaluate_rule(
    *,
    rule_type: str,
    parameters: dict[str, Any],
    severity: str,
    window: dict[str, Any],
    baseline: dict[str, Any] | None,
    account_watchlist: list[dict[str, Any]],
    narratives: list[dict[str, Any]],
) -> AlertHit | None:
    baseline = baseline or {}

    if rule_type == "absolute_volume":
        return _evaluate_absolute(parameters, severity, window)
    if rule_type == "rate_growth":
        return _evaluate_growth(parameters, severity, window, baseline)
    if rule_type == "anomaly":
        return _evaluate_anomaly(parameters, severity, window, baseline)
    if rule_type == "key_account":
        return _evaluate_key_account(parameters, severity, window, account_watchlist)
    if rule_type == "narrative":
        return _evaluate_narrative(parameters, severity, narratives)
    return None


def _evaluate_absolute(
    parameters: dict[str, Any],
    severity: str,
    window: dict[str, Any],
) -> AlertHit | None:
    metric = str(parameters.get("metric", "post_count"))
    threshold = float(parameters.get("threshold", 0))
    value = _metric_value(window, metric)
    if value < threshold:
        return None
    return AlertHit(
        rule_type="absolute_volume",
        severity=severity,
        fingerprint=_stable_fingerprint("absolute_volume", metric),
        explanation=f"{metric} 达到 {value:.0f}，超过阈值 {threshold:.0f}",
        metric_snapshot={"metric": metric, "value": value, "threshold": threshold},
        evidence_refs={"window": window.get("_window", {})},
    )


def _evaluate_growth(
    parameters: dict[str, Any],
    severity: str,
    window: dict[str, Any],
    baseline: dict[str, Any],
) -> AlertHit | None:
    metric = str(parameters.get("metric", "post_count"))
    min_ratio = float(parameters.get("min_growth_ratio", 2.0))
    min_baseline = int(parameters.get("min_baseline", 5))
    current = _metric_value(window, metric)
    previous = _metric_value(baseline, metric)
    if previous < min_baseline:
        return None
    ratio = current / previous if previous else 0.0
    if ratio < min_ratio:
        return None
    return AlertHit(
        rule_type="rate_growth",
        severity=severity,
        fingerprint=_stable_fingerprint("rate_growth", metric),
        explanation=(
            f"{metric} 从 {previous:.0f} 增长到 {current:.0f} "
            f"(x{ratio:.2f})，超过阈值 x{min_ratio:.2f}"
        ),
        metric_snapshot={
            "metric": metric,
            "current": current,
            "baseline": previous,
            "ratio": ratio,
            "min_ratio": min_ratio,
        },
        evidence_refs={"window": window.get("_window", {})},
    )


def _evaluate_anomaly(
    parameters: dict[str, Any],
    severity: str,
    window: dict[str, Any],
    baseline: dict[str, Any],
) -> AlertHit | None:
    metric = str(parameters.get("metric", "post_count"))
    min_samples = int(parameters.get("min_samples", 5))
    mad_threshold = float(parameters.get("mad_threshold", 3.0))
    history = baseline.get("history", {})
    series = [float(v) for v in history.get(metric, []) if isinstance(v, (int, float))]
    if len(series) < min_samples:
        # 样本不足是诊断状态，不制造风险告警。
        return None
    current = _metric_value(window, metric)
    m = median(series)
    dispersion = 1.4826 * _mad(series)
    if dispersion == 0:
        dispersion = 1.0
    z_score = (current - m) / dispersion
    if z_score < mad_threshold:
        return None
    return AlertHit(
        rule_type="anomaly",
        severity=severity,
        fingerprint=_stable_fingerprint("anomaly", metric),
        explanation=(
            f"{metric} 当前值 {current:.0f} 偏离中位数 {m:.0f} "
            f"(z={z_score:.2f}，MAD={dispersion:.2f})，超过阈值 {mad_threshold:.1f}"
        ),
        metric_snapshot={
            "metric": metric,
            "current": current,
            "median": m,
            "mad": dispersion,
            "z_score": z_score,
            "mad_threshold": mad_threshold,
            "samples": len(series),
        },
        evidence_refs={"window": window.get("_window", {})},
    )


def _match_watchlist(
    account_id: str | None,
    account_name: str | None,
    platform: str | None,
    watchlist: list[dict[str, Any]],
) -> dict[str, Any] | None:
    import unicodedata

    def norm(value: object) -> str:
        return unicodedata.normalize("NFKC", str(value or "")).lower()

    for entry in watchlist:
        if platform and entry.get("platform") and entry["platform"] != platform:
            continue
        w_norm = norm(entry.get("normalized_name") or entry.get("name"))
        w_id = str(entry.get("native_id") or entry.get("id") or "")
        if w_id and account_id and w_id == account_id:
            return {**entry, "matched_by": "id"}
        if w_norm and account_name and w_norm == norm(account_name):
            return {**entry, "matched_by": "normalized_name"}
        if entry.get("name") and account_name and entry["name"] == account_name:
            return {**entry, "matched_by": "name"}
    return None


def anomaly_baseline_status(
    baseline: dict[str, Any] | None,
    *,
    metric: str,
    min_samples: int = 5,
) -> str:
    """异常检测基线状态：insufficient / ok（诊断，不产生告警）。"""
    baseline = baseline or {}
    history = baseline.get("history", {})
    series = [
        float(v) for v in history.get(metric, []) if isinstance(v, (int, float))
    ]
    if len(series) < min_samples:
        return "insufficient"
    return "ok"


def _evaluate_key_account(
    parameters: dict[str, Any],
    severity: str,
    window: dict[str, Any],
    watchlist: list[dict[str, Any]],
) -> AlertHit | None:
    if not watchlist:
        return None
    accounts = window.get("accounts", [])
    hits: list[dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        match = _match_watchlist(
            account.get("id") or account.get("native_id"),
            account.get("name"),
            account.get("platform"),
            watchlist,
        )
        if match:
            hits.append({"account": account, "watchlist": match})
    if not hits:
        return None
    names = [h["watchlist"].get("name", "") for h in hits]
    return AlertHit(
        rule_type="key_account",
        severity=severity,
        fingerprint=_stable_fingerprint("key_account", *sorted(names)),
        explanation=f"观察名单账号介入：{', '.join(names)}",
        metric_snapshot={"matched_count": len(hits), "matched": names},
        evidence_refs={"accounts": [h["account"] for h in hits]},
    )


def _evaluate_narrative(
    parameters: dict[str, Any],
    severity: str,
    narratives: list[dict[str, Any]],
) -> AlertHit | None:
    min_sample = int(parameters.get("min_sample", 3))
    strong: list[dict[str, Any]] = []
    for narrative in narratives:
        if not isinstance(narrative, dict):
            continue
        sample = narrative.get("sample_size") or narrative.get("count") or 0
        if int(sample) >= min_sample:
            strong.append(narrative)
    if not strong:
        return None
    labels = [n.get("label") or n.get("version_id") or str(n) for n in strong]
    return AlertHit(
        rule_type="narrative",
        severity=severity,
        fingerprint=_stable_fingerprint("narrative", *sorted(labels)),
        explanation=f"新叙事形成：{', '.join(labels[:3])}（最小样本 {min_sample}）",
        metric_snapshot={"narrative_count": len(strong), "min_sample": min_sample},
        evidence_refs={"narratives": strong},
    )
