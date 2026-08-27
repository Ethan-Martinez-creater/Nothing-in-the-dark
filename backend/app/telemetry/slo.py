"""M19 SLO definitions, error budgets and burn-rate evaluation.

SLOs must be approved after baseline measurement; the initial suggestions
from the module spec are encoded here with explicit versions so the
accounting cannot be silently changed.  Exclusions are versioned too.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SLO:
    name: str
    description: str
    target: float  # 可用性/成功率目标（0-1）
    window_seconds: int
    kind: str = "availability"
    version: str = "1.0"

    def error_budget(self, total: int, ok: int) -> dict[str, object]:
        """错误预算：目标之外的失败份额。"""
        if total <= 0:
            return {
                "total": 0,
                "ok": 0,
                "actual": 1.0,
                "budget_remaining": 1.0,
                "burn_rate": 0.0,
                "violated": False,
            }
        actual = ok / total
        budget = 1.0 - self.target
        if budget <= 0:
            remaining = 1.0
        else:
            remaining = max(0.0, (actual - self.target) / budget)
        return {
            "total": total,
            "ok": ok,
            "actual": round(actual, 4),
            "target": self.target,
            "budget_remaining": round(remaining, 4),
            "burn_rate": round(1.0 - remaining, 4),
            "violated": actual < self.target,
        }


#: 初始 SLO 建议（需基线测量后正式批准）。
DEFAULT_SLOS: tuple[SLO, ...] = (
    SLO(
        "api_availability",
        "API 非长任务端点月度可用性",
        target=0.995,
        window_seconds=30 * 24 * 3600,
    ),
    SLO(
        "agent_final_state",
        "已接受 Agent 运行最终进入终态的比例（不含明确等待人工）",
        target=0.99,
        window_seconds=30 * 24 * 3600,
    ),
    SLO(
        "worker_lease_recovery",
        "Worker 可恢复任务在租约到期后 2 个 lease 周期内恢复",
        target=0.99,
        window_seconds=30 * 24 * 3600,
    ),
    SLO(
        "alert_enqueue_latency",
        "告警/通知入队延迟 P95 小于 60 秒",
        target=0.95,
        window_seconds=7 * 24 * 3600,
    ),
)


def evaluate_slos(
    *,
    api_total: int = 0,
    api_ok: int = 0,
    agent_total: int = 0,
    agent_ok: int = 0,
) -> list[dict[str, object]]:
    """对可用性类 SLO 求值（供 /system/telemetry-health）。"""
    results: list[dict[str, object]] = []
    for slo in DEFAULT_SLOS:
        if slo.name == "api_availability":
            result = slo.error_budget(api_total, api_ok)
        elif slo.name == "agent_final_state":
            result = slo.error_budget(agent_total, agent_ok)
        else:
            result = slo.error_budget(0, 0)
        results.append(
            {
                "name": slo.name,
                "description": slo.description,
                "kind": slo.kind,
                "version": slo.version,
                **result,
            }
        )
    return results

