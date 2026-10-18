"""Fresh-config gate and passive SDK observation for Issue #1.

All requests delegate to the production gateway. No application container,
production database, browser credentials or alternate model implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values

from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.llm.gateway import (
    LLMMessage,
    ModelRoute,
    OpenAICompatibleGateway,
)

REQUIRED = (
    "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_FAST_MODEL",
    "LLM_REASONING_MODEL", "LLM_REPORT_MODEL",
)
DEFAULT_RECEIPT = Path("artifacts/agent_eval/real_v1/preflight.json")
_ROUTE: ContextVar[str] = ContextVar("evaluation_model_route", default="unknown")


def load_fresh_settings(path: str | Path) -> tuple[Settings, str]:
    """Only this explicit file supplies LLM values; inherited old settings lose."""
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise ValueError("WAITING_FOR_NEW_LLM_CONFIG: explicit config file missing")
    values = dotenv_values(config_path, encoding="utf-8")
    missing = [key for key in REQUIRED if not str(values.get(key) or "").strip()]
    if missing:
        raise ValueError("WAITING_FOR_NEW_LLM_CONFIG: replace " + ", ".join(missing))
    endpoint = urlsplit(str(values["LLM_BASE_URL"]))
    if (
        endpoint.scheme != "https" or not endpoint.hostname
        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
    ):
        raise ValueError("LLM_BASE_URL must be HTTPS without credentials/query/fragment")
    if endpoint.path.rstrip("/").endswith("/chat/completions"):
        raise ValueError("LLM_BASE_URL must be the API root, not /chat/completions")
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        llm_provider=str(values["LLM_PROVIDER"]),
        llm_base_url=str(values["LLM_BASE_URL"]),
        llm_api_key=str(values["LLM_API_KEY"]),
        llm_fast_model=str(values["LLM_FAST_MODEL"]),
        llm_reasoning_model=str(values["LLM_REASONING_MODEL"]),
        llm_report_model=str(values["LLM_REPORT_MODEL"]),
        llm_max_concurrency=1,
        llm_timeout_seconds=min(float(values.get("LLM_TIMEOUT_SECONDS") or 60), 120),
        llm_max_retries=min(int(values.get("LLM_MAX_RETRIES") or 1), 1),
    )
    fingerprint = hashlib.sha256(json.dumps({
        **{key: values[key] for key in REQUIRED},
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_retries": settings.llm_max_retries,
        "max_concurrency": settings.llm_max_concurrency,
    }, sort_keys=True).encode("utf-8")).hexdigest()
    return settings, fingerprint


def config_metadata(settings: Settings) -> dict[str, Any]:
    endpoint = urlsplit(settings.llm_base_url)
    return {
        "provider_label": settings.llm_provider,
        "transport": "OpenAICompatibleGateway/chat.completions",
        "base_url_origin": f"{endpoint.scheme}://{endpoint.netloc}",
        "base_url_path_sha256": hashlib.sha256(endpoint.path.encode()).hexdigest(),
        "effective_routes": {str(route): settings.llm_fast_model for route in ModelRoute},
        "declared_models": {
            "fast": settings.llm_fast_model,
            "reasoning": settings.llm_reasoning_model,
            "report": settings.llm_report_model,
        },
        "routing_policy": "production_all_routes_use_fast_model",
        "pricing_status": "NOT_MEASURABLE: historical table not verified for fresh provider",
    }


class ObservedProductionGateway:
    """Observe raw usage before production's missing-usage-to-zero conversion."""

    def __init__(self, gateway: Any, *, allowed_model: str) -> None:
        self._gateway = gateway
        self._allowed_model = allowed_model
        self.evaluation_measurements: list[dict[str, Any]] = []
        self.hard_stop_reason: str | None = None
        self._consecutive_errors = 0
        original = gateway._client.chat.completions.create

        async def capture(**kwargs: Any) -> Any:
            if self.hard_stop_reason:
                raise ApplicationError("Evaluation stopped", code="evaluation_hard_stop")
            started = time.monotonic()
            entry: dict[str, Any] = {
                "route": _ROUTE.get(), "requested_model": kwargs.get("model"),
                "actual_model": None, "status": "failed", "input_tokens": None,
                "output_tokens": None, "cached_input_tokens": None,
                "error_code": None, "cost": None, "currency": None,
                "pricing_status": "NOT_MEASURABLE",
            }
            try:
                response = await original(**kwargs)
                entry["actual_model"] = getattr(response, "model", None)
                if entry["actual_model"] != self._allowed_model:
                    self.hard_stop_reason = "unexpected_response_model"
                    raise ApplicationError(
                        "Unexpected model returned; update explicit configuration before retry",
                        code="evaluation_model_route_mismatch",
                    )
                usage = getattr(response, "usage", None)
                entry["input_tokens"] = getattr(usage, "prompt_tokens", None)
                entry["output_tokens"] = getattr(usage, "completion_tokens", None)
                details = getattr(usage, "prompt_tokens_details", None)
                entry["cached_input_tokens"] = getattr(
                    usage, "prompt_cache_hit_tokens", None
                )
                if entry["cached_input_tokens"] is None:
                    entry["cached_input_tokens"] = getattr(details, "cached_tokens", None)
                entry["usage_status"] = (
                    "MEASURED" if entry["input_tokens"] is not None
                    and entry["output_tokens"] is not None else "NOT_MEASURABLE"
                )
                entry["status"] = "succeeded"
                self._consecutive_errors = 0
                return response
            except Exception as exc:
                self._consecutive_errors += 1
                status = getattr(exc, "status_code", None)
                entry["error_code"] = getattr(exc, "code", None) or (
                    f"http_{status}" if status else type(exc).__name__
                )
                if status in {401, 403}:
                    self.hard_stop_reason = "provider_auth_or_permission_error"
                elif self._consecutive_errors >= 3:
                    self.hard_stop_reason = "repeated_provider_errors"
                raise
            finally:
                entry["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
                self.evaluation_measurements.append(entry)

        gateway._client.chat.completions.create = capture

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gateway, name)

    async def complete(self, *, route: ModelRoute, **kwargs: Any) -> Any:
        if self.hard_stop_reason:
            raise ApplicationError("Evaluation stopped", code="evaluation_hard_stop")
        token = _ROUTE.set(str(route))
        try:
            return await self._gateway.complete(route=route, **kwargs)
        finally:
            _ROUTE.reset(token)

    async def aclose(self) -> None:
        await self._gateway._client.close()


def verified_gateway(config_path: str | Path, receipt_path: str | Path) -> Any:
    settings, fingerprint = load_fresh_settings(config_path)
    try:
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
        at = datetime.fromisoformat(receipt["verified_at"])
        valid = (
            receipt.get("status") == "verified"
            and receipt.get("config_fingerprint") == fingerprint
            and datetime.now(UTC) - timedelta(hours=24) <= at <= datetime.now(UTC)
            and receipt.get("user_confirmed_fresh") is True
        )
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError("WAITING_FOR_NEW_LLM_CONFIG: fresh authenticated preflight required")
    return ObservedProductionGateway(
        OpenAICompatibleGateway(settings), allowed_model=settings.llm_fast_model
    )


async def probe(config_path: str, receipt_path: str, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("Confirm the user replaced the full configuration before any API call")
    target = Path(receipt_path)
    if await asyncio.to_thread(target.exists):
        raise ValueError("Preflight receipt already exists; preserve it and choose a new path")
    settings, fingerprint = load_fresh_settings(config_path)
    # Keep the authenticated probe bounded; no automatic transport retries.
    settings.llm_max_retries = 0
    gateway = ObservedProductionGateway(
        OpenAICompatibleGateway(settings), allowed_model=settings.llm_fast_model
    )
    receipt: dict[str, Any] = {
        "status": "failed", "user_confirmed_fresh": True,
        "config_fingerprint": fingerprint, "model_config": config_metadata(settings),
        "verified_at": datetime.now(UTC).isoformat(),
        "real_model_suite_status": "REAL_MODEL_NOT_RUN",
    }
    tool = {"type": "function", "function": {
        "name": "preflight_echo", "description": "Return the supplied test string.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False},
    }}
    try:
        first = await gateway.complete(
            messages=[LLMMessage(role="user", content=(
                "Tool protocol compatibility check only. Call preflight_echo with value READY."
            ))], tools=[tool], route=ModelRoute.FAST,
        )
        if len(first.tool_calls) != 1 or first.tool_calls[0].name != "preflight_echo":
            raise ValueError("Expected one preflight tool call")
        call = first.tool_calls[0]
        if call.arguments != {"value": "READY"}:
            raise ValueError("Preflight tool arguments mismatch")
        second = await gateway.complete(
            messages=[
                LLMMessage(role="user", content="Call preflight_echo with value READY."),
                first.message,
                LLMMessage(role="tool", tool_call_id=call.id, content='{"value":"READY"}'),
            ], tools=[], route=ModelRoute.REPORT,
        )
        if not (second.message.content or "").strip() or second.tool_calls:
            raise ValueError("Preflight tool result was not consumed as a final answer")
        receipt["status"] = "verified"
        receipt["tool_roundtrip"] = True
        receipt["returned_models"] = sorted({
            m["actual_model"] for m in gateway.evaluation_measurements
        })
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
        receipt["error_code"] = getattr(exc, "code", None)
    finally:
        receipt["measurements"] = gateway.evaluation_measurements
        await gateway.aclose()
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text, json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify fresh Issue #1 model configuration")
    parser.add_argument("--config-file", default=".env")
    parser.add_argument("--receipt", default=str(DEFAULT_RECEIPT))
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--confirm-fresh-config", action="store_true")
    args = parser.parse_args()
    if not args.probe:
        settings, _fingerprint = load_fresh_settings(args.config_file)
        print(json.dumps({"status": "CONFIG_PRESENT_NOT_VERIFIED", **config_metadata(settings)},
                         ensure_ascii=False, indent=2))
        return 0
    receipt = asyncio.run(probe(
        args.config_file, args.receipt, confirmed=args.confirm_fresh_config
    ))
    # Private receipt includes a secret-bound fingerprint; console never needs it.
    print(json.dumps({key: value for key, value in receipt.items()
                      if key != "config_fingerprint"}, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
