"""M22: resilience primitives - failure classification, retry, circuit breaking.

故障隔离、降级与事故处置的核心确定性逻辑（无 IO、无 LLM，便于单元测试）：

- 八类稳定错误分类 + retryability + scope（解析异常字符串不做核心决策）。
- RetryPolicy：指数退避 + 抖动 + Retry-After 尊重 + 总时间预算。
- CircuitBreaker：closed/open/half_open 状态机，滚动窗口失败率，FakeClock 可测。
- Bulkhead/AdmissionController：并发隔离与背压准入。
- StuckDetector：基于心跳/阶段最大时长/子进程存在性，不以总运行时长判定。
- Kill Switch 层级覆盖：global > dependency/platform > tool，低层配置不得绕过高层。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------- 错误分类（稳定 code，不解析异常字符串） ----------

CLASS_TRANSIENT = "transient"
CLASS_RATE_LIMITED = "rate_limited"
CLASS_AUTH_REQUIRED = "auth_required"
CLASS_PERMANENT_INPUT = "permanent_input"
CLASS_POLICY_DENIED = "policy_denied"
CLASS_RESOURCE_EXHAUSTED = "resource_exhausted"
CLASS_DEPENDENCY_OUTAGE = "dependency_outage"
CLASS_UNKNOWN = "unknown"

CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        CLASS_TRANSIENT,
        CLASS_RATE_LIMITED,
        CLASS_AUTH_REQUIRED,
        CLASS_PERMANENT_INPUT,
        CLASS_POLICY_DENIED,
        CLASS_RESOURCE_EXHAUSTED,
        CLASS_DEPENDENCY_OUTAGE,
        CLASS_UNKNOWN,
    }
)

#: 每类错误是否允许自动重试（决策层唯一依据）。
RETRYABLE: dict[str, bool] = {
    CLASS_TRANSIENT: True,
    CLASS_RATE_LIMITED: True,
    CLASS_AUTH_REQUIRED: False,  # 登录失效/二维码等待 -> 请求人工，不自动重试
    CLASS_PERMANENT_INPUT: False,
    CLASS_POLICY_DENIED: False,  # 不以技术重试绕过策略
    CLASS_RESOURCE_EXHAUSTED: False,  # 触发背压而非重试
    CLASS_DEPENDENCY_OUTAGE: True,  # 有限重试后熔断降级
    CLASS_UNKNOWN: True,  # 有限次数后进入死信与人工调查
}

#: 依赖作用域（决定熔断/隔离的粒度）。
SCOPE_PLATFORM = "platform"
SCOPE_MODEL = "model"
SCOPE_TOOL = "tool"
SCOPE_MEDIA = "media"
SCOPE_NOTIFICATION = "notification"
SCOPE_DATABASE = "database"


@dataclass(frozen=True, slots=True)
class FailureClassification:
    """一次失败的稳定分类结果。"""

    classification: str
    retryable: bool
    scope: str = SCOPE_TOOL
    error_code: str = ""
    retry_after_seconds: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "retryable": self.retryable,
            "scope": self.scope,
            "error_code": self.error_code,
            "retry_after_seconds": self.retry_after_seconds,
            "detail": self.detail,
        }


def classify_exception(
    exc: BaseException | None,
    *,
    status_code: int | None = None,
    scope: str = SCOPE_TOOL,
    error_code: str = "",
    retry_after: float | None = None,
) -> FailureClassification:
    """把任意异常映射为稳定分类。

    优先级：显式 status_code > 异常类型 > 错误码后缀 > unknown。
    核心决策只依赖分类，不解析异常字符串内容。
    """
    if status_code is not None:
        if status_code == 429 or (status_code == 403 and retry_after is not None):
            return FailureClassification(
                CLASS_RATE_LIMITED, True, scope, error_code or f"http_{status_code}",
                retry_after, "rate limited",
            )
        if status_code in {401, 403}:
            return FailureClassification(
                CLASS_AUTH_REQUIRED, False, scope, error_code or f"http_{status_code}",
                None, "authentication required",
            )
        if 400 <= status_code < 500:
            return FailureClassification(
                CLASS_PERMANENT_INPUT, False, scope, error_code or f"http_{status_code}",
                None, "permanent client error",
            )
        if status_code in {500, 502, 503, 504}:
            return FailureClassification(
                CLASS_TRANSIENT, True, scope, error_code or f"http_{status_code}",
                None, "transient server error",
            )

    name = type(exc).__name__ if exc is not None else ""
    name_lower = name.lower()
    code = (error_code or "").lower()
    if (
        "timeout" in name_lower
        or "connection" in name_lower
        or "brokenpipe" in name_lower
    ):
        return FailureClassification(
            CLASS_TRANSIENT, True, scope, error_code or "network_error",
            None, "network timeout or connection reset",
        )
    if name in {"KeyboardInterrupt", "SystemExit"}:
        return FailureClassification(
            CLASS_PERMANENT_INPUT, False, scope, error_code or name, None, "control flow",
        )
    if code in {
        "llm_not_configured",
        "invalid_tool_arguments",
        "resource_not_found",
        "approval_required",
        "approval_already_decided",
        "tool_input_blocked",
    }:
        return FailureClassification(
            CLASS_PERMANENT_INPUT, False, scope, error_code or code, None,
            "permanent application error",
        )
    if code in {"budget_exhausted", "queue_capacity_exceeded", "disk_watermark"}:
        return FailureClassification(
            CLASS_RESOURCE_EXHAUSTED, False, scope, error_code or code,
            None, "resource exhausted",
        )
    if code in {"sandbox_denied", "policy_denied", "egress_blocked", "secret_policy"}:
        return FailureClassification(
            CLASS_POLICY_DENIED, False, scope, error_code or code,
            None, "policy denied",
        )
    if code in {"dependency_outage", "provider_outage"}:
        return FailureClassification(
            CLASS_DEPENDENCY_OUTAGE, True, scope, error_code or code,
            None, "dependency outage",
        )
    return FailureClassification(
        CLASS_UNKNOWN, True, scope, error_code or name or "unknown",
        None, "unclassified failure",
    )


# ---------- RetryPolicy ----------

@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """按错误类配置最大次数、指数退避、抖动与总时间预算。

    next_backoff 尊重 Retry-After（rate_limited 时优先）。
    """

    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    jitter_ratio: float = 0.2  # 0~1，抖动幅度（指数值比例）
    total_time_budget_seconds: float = 120.0
    respect_retry_after: bool = True

    def exhausted(self, attempt: int) -> bool:
        return attempt >= self.max_attempts

    def within_budget(self, elapsed_seconds: float) -> bool:
        return elapsed_seconds < self.total_time_budget_seconds

    def next_backoff(
        self,
        attempt: int,
        *,
        retry_after: float | None = None,
        rng: random.Random | None = None,
    ) -> float:
        """第 attempt 次失败后的等待秒数（attempt 从 1 开始）。

        - Retry-After 存在时优先（rate_limited / 403+429）。
        - 指数退避 min(base * 2^(attempt-1), max_backoff)。
        - 抖动：均匀 ±jitter_ratio（保证 >0）。
        """
        if retry_after is not None and self.respect_retry_after:
            return max(0.0, float(retry_after))
        exponential = min(
            self.base_backoff_seconds * (2 ** max(0, attempt - 1)),
            self.max_backoff_seconds,
        )
        rng = rng or random
        jitter = exponential * self.jitter_ratio
        return max(0.0, exponential + rng.uniform(-jitter, jitter))


# ---------- CircuitBreaker（FakeClock 可测） ----------

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


@dataclass(slots=True)
class CircuitBreaker:
    """滚动窗口失败率熔断器（单依赖）。

    状态机：closed -> open（连续失败/窗口失败率）-> half_open（半开超时）
    -> closed（成功阈值）| open（半开探测失败）。auth/policy 类错误由
    上层单独计数，不进入普通可用性熔断。
    """

    failure_threshold: int = 5
    min_request_count: int = 3
    window_seconds: float = 60.0
    half_open_timeout_seconds: float = 30.0
    half_open_success_threshold: int = 2
    config_version: str = "1.0"

    state: str = STATE_CLOSED
    failure_count: int = 0
    success_count: int = 0
    window_started_at: float = 0.0
    opened_at: float | None = None
    half_open_probe_at: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CircuitBreaker:
        breaker = cls(
            failure_threshold=int(data.get("failure_threshold") or cls.failure_threshold),
            min_request_count=int(data.get("min_request_count") or cls.min_request_count),
            window_seconds=float(data.get("window_seconds") or cls.window_seconds),
            half_open_timeout_seconds=float(
                data.get("half_open_timeout_seconds") or cls.half_open_timeout_seconds
            ),
            half_open_success_threshold=int(
                data.get("half_open_success_threshold") or cls.half_open_success_threshold
            ),
            config_version=str(data.get("config_version") or cls.config_version),
        )
        breaker.state = str(data.get("state") or STATE_CLOSED)
        breaker.failure_count = int(data.get("failure_count") or 0)
        breaker.success_count = int(data.get("success_count") or 0)
        breaker.opened_at = data.get("opened_at")
        breaker.half_open_probe_at = data.get("half_open_probe_at")
        return breaker

    def allow_request(self, now: float) -> bool:
        """open 状态在窗口内拒绝；半开超时后允许一次探测。"""
        if self.state == STATE_OPEN:
            if (
                self.opened_at is not None
                and (now - self.opened_at) >= self.half_open_timeout_seconds
            ):
                self.state = STATE_HALF_OPEN
                self.half_open_probe_at = now
                self.success_count = 0
                self.failure_count = 0
                return True
            return False
        return True

    def record_success(self, now: float) -> None:
        if self.state == STATE_HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_success_threshold:
                self._close(now)
        else:
            self.failure_count = 0
            self.success_count += 1

    def record_failure(self, now: float) -> None:
        if self.state == STATE_HALF_OPEN:
            self._open(now)
            return
        self.failure_count += 1
        total = self.failure_count + self.success_count
        if self.failure_count >= self.failure_threshold or (
            total >= self.min_request_count
            and self._window_failure_rate(now) >= 0.5
        ):
            self._open(now)

    def reset(self) -> None:
        self.state = STATE_CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.window_started_at = 0.0
        self.opened_at = None
        self.half_open_probe_at = None

    def _window_failure_rate(self, now: float) -> float:
        elapsed = now - self.window_started_at if self.window_started_at else 0.0
        if elapsed >= self.window_seconds:
            return 0.0
        total = self.failure_count + self.success_count
        return self.failure_count / total if total else 0.0

    def _open(self, now: float) -> None:
        self.state = STATE_OPEN
        self.opened_at = now
        self.half_open_probe_at = None
        self.failure_count = 0
        self.success_count = 0

    def _close(self, now: float) -> None:
        self.state = STATE_CLOSED
        self.opened_at = None
        self.half_open_probe_at = None
        self.failure_count = 0
        self.success_count = 0
        self.window_started_at = now

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "window_started_at": self.window_started_at,
            "opened_at": self.opened_at,
            "half_open_probe_at": self.half_open_probe_at,
            "config_version": self.config_version,
            "thresholds": {
                "failure_threshold": self.failure_threshold,
                "min_request_count": self.min_request_count,
                "window_seconds": self.window_seconds,
                "half_open_timeout_seconds": self.half_open_timeout_seconds,
                "half_open_success_threshold": self.half_open_success_threshold,
            },
        }


# ---------- Bulkhead / Admission ----------

@dataclass(frozen=True, slots=True)
class BulkheadConfig:
    """每类依赖的并发/队列上限，防止单类任务耗尽 Worker。"""

    max_concurrency: int
    max_queue: int

    def to_dict(self) -> dict[str, object]:
        return {"max_concurrency": self.max_concurrency, "max_queue": self.max_queue}


DEFAULT_BULKHEADS: dict[str, BulkheadConfig] = {
    SCOPE_PLATFORM: BulkheadConfig(max_concurrency=2, max_queue=100),
    SCOPE_MODEL: BulkheadConfig(max_concurrency=3, max_queue=200),
    SCOPE_MEDIA: BulkheadConfig(max_concurrency=1, max_queue=50),
    SCOPE_NOTIFICATION: BulkheadConfig(max_concurrency=2, max_queue=500),
    SCOPE_DATABASE: BulkheadConfig(max_concurrency=8, max_queue=1000),
    SCOPE_TOOL: BulkheadConfig(max_concurrency=4, max_queue=300),
}


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """准入判定：admitted / deferred / rejected + 可解释原因。"""

    decision: str  # admitted / deferred / rejected
    reason: str
    retry_after_seconds: float | None = None

    @property
    def admitted(self) -> bool:
        return self.decision == "admitted"


@dataclass(frozen=True, slots=True)
class AdmissionController:
    """背压与准入：队列深度、预计等待、DB/磁盘水位与预算进入判定。

    高优先级人工动作保留受控配额（reserved_slots）。
    """

    queue_capacity: int = 200
    max_wait_seconds: float = 60.0
    db_watermark: float = 0.9
    disk_watermark: float = 0.95
    budget_exhausted: bool = False
    reserved_slots: int = 4

    def admit(
        self,
        *,
        queue_depth: int,
        estimated_wait_seconds: float,
        db_usage: float = 0.0,
        disk_usage: float = 0.0,
        is_priority: bool = False,
    ) -> AdmissionDecision:
        if self.budget_exhausted and not is_priority:
            return AdmissionDecision(
                "rejected", "预算已耗尽（资源类错误触发背压，不重试）"
            )
        if db_usage >= self.db_watermark:
            return AdmissionDecision(
                "deferred", "数据库水位达到阈值，延迟准入", retry_after_seconds=5.0
            )
        if disk_usage >= self.disk_watermark:
            return AdmissionDecision(
                "deferred", "磁盘水位达到阈值，延迟准入", retry_after_seconds=30.0
            )
        effective_capacity = self.queue_capacity - (
            0 if is_priority else self.reserved_slots
        )
        if queue_depth >= effective_capacity:
            return AdmissionDecision(
                "rejected",
                f"队列深度 {queue_depth} 达到容量上限 {effective_capacity}",
                retry_after_seconds=10.0,
            )
        if estimated_wait_seconds > self.max_wait_seconds:
            return AdmissionDecision(
                "deferred",
                f"预计等待 {estimated_wait_seconds:.1f}s 超过上限",
                retry_after_seconds=5.0,
            )
        return AdmissionDecision("admitted", "准入")


# ---------- Stuck Detector ----------

@dataclass(frozen=True, slots=True)
class StuckDetector:
    """卡死判定：心跳、阶段最大时长与子进程存在性。

    长任务不以总运行时间判定卡死：只要心跳新鲜或子进程仍存活，就不算
    卡死（模型长推理/长爬取是合法的）。
    """

    heartbeat_stale_seconds: float = 300.0
    stage_max_seconds: float = 3600.0

    def is_stuck(
        self,
        *,
        last_heartbeat_at: datetime | None,
        stage_started_at: datetime | None,
        stage: str = "",
        process_alive: bool = True,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)

        def _age(value: datetime | None) -> float:
            if value is None:
                return float("inf")
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return max(0.0, (now - value.astimezone(UTC)).total_seconds())

        if process_alive and _age(last_heartbeat_at) <= self.heartbeat_stale_seconds:
            return False
        if process_alive and _age(stage_started_at) <= self.stage_max_seconds:
            return False
        return True


# ---------- 降级路由 ----------

@dataclass(frozen=True, slots=True)
class FallbackDecision:
    """降级决策：是否降级、实际使用的模型/Provider、限制说明。"""

    degraded: bool
    actual_model: str
    reason: str
    capability_drop: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "degraded": self.degraded,
            "actual_model": self.actual_model,
            "reason": self.reason,
            "capability_drop": list(self.capability_drop),
        }


def choose_fallback_route(
    *,
    primary_model: str,
    primary_healthy: bool,
    fallback_models: list[str],
    fallback_health: dict[str, bool],
    capabilities_compatible: bool = True,
) -> FallbackDecision | None:
    """模型/Provider 降级路由：健康检查 + schema/安全/成本政策兼容性。

    返回 None 表示无可用降级（走死信/人工）。
    """
    if primary_healthy:
        return None
    for candidate in fallback_models:
        if fallback_health.get(candidate, False) and capabilities_compatible:
            return FallbackDecision(
                degraded=True,
                actual_model=candidate,
                reason=f"主模型 {primary_model} 不可用，降级到 {candidate}",
            )
    return FallbackDecision(
        degraded=True,
        actual_model="",
        reason="主模型不可用且无兼容降级",
        capability_drop=["llm"],
    )


# ---------- Kill Switch 层级 ----------

#: 层级顺序：越高越优先；低层配置不得绕过高层停止。
KILL_SWITCH_LEVELS: dict[str, int] = {
    "global": 3,
    "dependency": 2,
    "platform": 2,
    "tool": 1,
}


def kill_switch_active(
    switches: list[dict[str, str]],
    *,
    scope: str,
    target: str,
) -> tuple[bool, str]:
    """判定 (scope, target) 是否被 Kill Switch 停止。

    层级覆盖：global on 时任何目标都停止（即使低层配置为 off）；
    同层 target='*' 覆盖所有具体目标；不存在"低层关闭"覆盖高层开启。
    """
    for switch in switches:
        if switch.get("status") != "on":
            continue
        s = str(switch.get("scope") or "")
        t = str(switch.get("target") or "")
        if s == scope and (t == "*" or t == target):
            return True, f"kill_switch:{s}:{t}"
    active = [
        (KILL_SWITCH_LEVELS.get(str(sw.get("scope") or ""), 0), sw)
        for sw in switches
        if sw.get("status") == "on"
    ]
    if not active:
        return False, ""
    highest_level = max(level for level, _ in active)
    current_level = KILL_SWITCH_LEVELS.get(scope, 0)
    if highest_level > current_level:
        top = max(active, key=lambda item: item[0])[1]
        return True, f"kill_switch:{top.get('scope')}:{top.get('target')}"
    if highest_level == current_level:
        for _, sw in active:
            if sw.get("target") == "*":
                return True, f"kill_switch:{sw.get('scope')}:*"
    return False, ""
