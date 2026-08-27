"""M20 application service: dataset registration, runs, gates, drift."""

from __future__ import annotations

from typing import Any

from app.application.repositories import ApplicationRepository
from app.core.errors import ApplicationError
from app.services.evaluation import EvaluatorRegistry, build_default_registry
from app.services.quality_gate import (
    GATE_BLOCK,
    NON_EXEMPTIBLE_METRICS,
    QualityGateError,
    ReleaseGate,
    bootstrap_ci,
    content_hash,
    drift_alert,
    validate_examples,
)


class EvaluationServiceError(ApplicationError):
    pass


class EvaluationService:
    """编排评测运行与门禁判定。"""

    def __init__(
        self, repository: ApplicationRepository, registry: EvaluatorRegistry | None = None
    ) -> None:
        self._repository = repository
        self._registry = registry or build_default_registry()

    # -- dataset governance ------------------------------------------------

    async def register_dataset(
        self,
        manifest: dict[str, Any],
        examples: list[dict[str, Any]],
    ) -> dict[str, object]:
        from app.services.quality_gate import DatasetManifest

        try:
            parsed = DatasetManifest(
                name=str(manifest.get("name") or ""),
                version=str(manifest.get("version") or "1.0.0"),
                task=str(manifest.get("task") or ""),
                source=str(manifest.get("source") or ""),
                license=str(manifest.get("license") or ""),
                time_range=dict(manifest.get("time_range") or {}),
                platforms=list(manifest.get("platforms") or []),
                schema_version=str(manifest.get("schema_version") or "1.0"),
                train_holdout=bool(manifest.get("train_holdout") or False),
            ).validate()
        except QualityGateError as exc:
            raise EvaluationServiceError(
                str(exc), code="dataset_manifest_invalid",
            ) from exc
        problems = validate_examples(examples)
        if problems:
            raise EvaluationServiceError(
                "Dataset examples invalid: " + "; ".join(problems[:5]),
                code="dataset_examples_invalid",
            )
        normalized_examples: list[dict[str, Any]] = []
        for example in examples:
            normalized = dict(example)
            if not normalized.get("input_hash"):
                normalized["input_hash"] = content_hash(
                    {"input": normalized.get("input")}
                )
            normalized_examples.append(normalized)
        manifest_hash = parsed.manifest_hash(normalized_examples)
        manifest_payload = {
            **parsed.to_dict(),
            "example_count": len(normalized_examples),
        }
        record = await self._repository.create_dataset_manifest(
            manifest_payload, manifest_hash
        )
        persisted_examples = [
            {
                **example,
                "gold": {
                    "input": example.get("input"),
                    "gold": example.get("gold"),
                },
            }
            for example in normalized_examples
        ]
        count = await self._repository.add_dataset_examples(
            record.id, persisted_examples
        )
        return {
            "manifest": record,
            "example_count": count,
            "content_hash": manifest_hash,
        }

    # -- evaluation runs ---------------------------------------------------

    async def run_evaluation(
        self,
        *,
        suite: str,
        candidate_version: str,
        baseline_version: str,
        dataset_manifest_id: str,
        evaluator_names: list[str] | None = None,
        commit: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        manifest = await self._repository.get_dataset_manifest(
            dataset_manifest_id
        )
        examples_rows = await self._repository.list_dataset_examples(
            dataset_manifest_id
        )
        examples = []
        for row in examples_rows:
            if isinstance(row.gold, dict):
                examples.append(
                    {
                        "example_id": row.example_id,
                        "input": row.gold.get("input"),
                        "gold": row.gold.get("gold"),
                    }
                )
            else:
                examples.append(
                    {
                        "example_id": row.example_id,
                        "input": row.gold,
                        "gold": row.gold,
                    }
                )
        run = await self._repository.create_evaluation_run(
            suite=suite,
            candidate_version=candidate_version,
            baseline_version=baseline_version,
            dataset_manifest_id=dataset_manifest_id,
            commit=commit,
            config=config,
        )
        names = evaluator_names or self._registry.names()
        outcome = self._registry.run_suite(names, examples, config or {})
        aggregate = self._aggregate(outcome["results"])
        run_record = await self._repository.finish_evaluation_run(
            run.id,
            status="completed" if outcome["all_passed"] else "partial_failed",
            results=outcome["results"],
            aggregate=aggregate,
            differences=[],
            error_samples=outcome["failed"],
        )
        return {
            "run": run_record,
            "results": outcome["results"],
            "aggregate": aggregate,
            "failed": outcome["failed"],
            "manifest": manifest,
        }

    @staticmethod
    def _aggregate(results: dict[str, Any]) -> dict[str, float]:
        """把各评测器结果聚合成整体指标（取关键 metric）。"""
        aggregate: dict[str, float] = {}
        for name, result in results.items():
            if not isinstance(result, dict):
                continue
            for key in ("macro_f1", "f1", "citation_correctness", "precision", "recall"):
                if key in result and isinstance(result[key], (int, float)):
                    aggregate[name + "." + key] = round(float(result[key]), 4)
        return aggregate

    # -- release gates ------------------------------------------------------

    async def evaluate_gates(
        self,
        run_id: str,
        *,
        exempted_by: str | None = None,
        exempt_reason: str | None = None,
    ) -> list[dict[str, object]]:
        """对一次评测运行跑所有启用门禁；关键安全指标不可豁免。"""
        run = await self._repository.get_evaluation_run(run_id)
        gates = await self._repository.list_release_gates(suite=run.suite)
        metrics: dict[str, float] = {}
        for name, value in (run.aggregate or {}).items():
            try:
                metrics[name] = float(value)
            except (TypeError, ValueError):
                continue
        for result in (run.results or {}).values():
            if not isinstance(result, dict):
                continue
            for key in NON_EXEMPTIBLE_METRICS:
                value = result.get(key)
                if isinstance(value, (int, float)):
                    metrics[key] = float(value)
        results: list[dict[str, object]] = []
        for gate in gates:
            definition = ReleaseGate(
                name=gate.name,
                suite=gate.suite,
                thresholds=dict(gate.thresholds or {}),
                relative_regression_limits=dict(
                    gate.relative_regression_limits or {}
                ),
                mandatory=gate.mandatory,
                enabled=gate.enabled,
                version=gate.version,
            )
            baseline_metrics = dict(
                (run.config or {}).get("baseline_metrics") or {}
            )
            sample_sizes = dict((run.config or {}).get("sample_sizes") or {})
            outcome = definition.evaluate(
                metrics,
                baseline={
                    str(key): float(value)
                    for key, value in baseline_metrics.items()
                    if isinstance(value, (int, float))
                },
                sample_sizes={
                    str(key): int(value)
                    for key, value in sample_sizes.items()
                    if isinstance(value, (int, float))
                },
            )
            decision = outcome["decision"]
            violations = outcome.get("violations") or []
            if any(
                v.get("metric") in NON_EXEMPTIBLE_METRICS
                for v in violations
            ):
                decision = GATE_BLOCK
                outcome["reason"] = (
                    outcome.get("reason", "") + " [安全指标不可豁免]"
                )
            record = await self._repository.create_gate_result(
                gate_id=gate.id,
                evaluation_run_id=run_id,
                decision=decision,
                reason=outcome.get("reason", ""),
                details=outcome,
                exempted_by=exempted_by if decision == GATE_BLOCK else None,
                exempt_reason=exempt_reason if decision == GATE_BLOCK else None,
            )
            results.append(
                {
                    "gate_id": gate.id,
                    "gate_name": gate.name,
                    "decision": decision,
                    "reason": outcome.get("reason", ""),
                    "violations": violations,
                    "record_id": record.id,
                }
            )
        return results

    # -- online drift -------------------------------------------------------

    def online_drift(
        self,
        baseline: dict[str, float],
        current: dict[str, float],
    ) -> dict[str, object]:
        """在线分布漂移（PSI/JS）；漂移是调查信号，不自动降质。"""
        return drift_alert(baseline=baseline, current=current)

    def confidence_interval(
        self, differences: list[float],
    ) -> dict[str, object]:
        return bootstrap_ci(differences)

