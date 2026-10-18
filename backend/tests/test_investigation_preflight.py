"""No network: freshness, raw SDK usage and isolated study report DBs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evaluation.investigation_preflight import (
    ObservedProductionGateway,
    load_fresh_settings,
    probe,
    verified_gateway,
)
from app.infrastructure.llm.gateway import ModelRoute
from app.scripts.run_agent_eval import _investigation_database_url


def _config(path: Path, *, key: str = "new-test-key", model: str = "test-model") -> Path:
    path.write_text(
        "LLM_PROVIDER=openai_compatible\nLLM_BASE_URL=https://unit.invalid/v1\n"
        f"LLM_API_KEY={key}\nLLM_FAST_MODEL={model}\n"
        f"LLM_REASONING_MODEL={model}\nLLM_REPORT_MODEL={model}\n",
        encoding="utf-8",
    )
    return path


def test_explicit_config_wins_over_expired_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "expired-key-must-not-win")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://live.invalid/live")
    settings, _ = load_fresh_settings(_config(tmp_path / ".env"))
    assert settings.llm_api_key.get_secret_value() == "new-test-key"
    assert settings.database_url == "sqlite+aiosqlite:///:memory:"


def test_full_config_required_and_unsafe_endpoint_rejected(tmp_path):
    config = tmp_path / ".env"
    config.write_text("LLM_API_KEY=only-key\n", encoding="utf-8")
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        load_fresh_settings(config)
    _config(config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "https://unit.invalid/v1", "https://unit.invalid/v1?api_key=hidden"
        ), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="without credentials"):
        load_fresh_settings(config)


def test_real_gateway_rejects_missing_or_changed_receipt(tmp_path):
    config = _config(tmp_path / ".env")
    receipt = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="preflight required"):
        verified_gateway(config, receipt)
    _, fingerprint = load_fresh_settings(config)
    receipt.write_text(json.dumps({
        "status": "verified", "config_fingerprint": fingerprint,
        "verified_at": datetime.now(UTC).isoformat(), "user_confirmed_fresh": True,
    }), encoding="utf-8")
    _config(config, key="different-new-test-key")
    with pytest.raises(ValueError, match="preflight required"):
        verified_gateway(config, receipt)


async def test_probe_requires_fresh_confirmation_before_config_or_network(tmp_path):
    with pytest.raises(ValueError, match="Confirm the user"):
        await probe(str(tmp_path / "missing"), str(tmp_path / "receipt"), confirmed=False)


async def test_probe_refuses_existing_receipt_before_network(tmp_path):
    receipt = tmp_path / "receipt"
    receipt.write_text("preserved-failed-run", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        await probe(str(tmp_path / "missing"), str(receipt), confirmed=True)


class _FakeGateway:
    def __init__(self, response):
        async def create(**_kwargs):
            return response
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    async def complete(self, **_kwargs):
        return await self._client.chat.completions.create(model="test-model")


async def test_missing_raw_usage_is_none_and_route_is_observed():
    gateway = ObservedProductionGateway(
        _FakeGateway(SimpleNamespace(model="test-model", usage=None)),
        allowed_model="test-model",
    )
    await gateway.complete(route=ModelRoute.REPORT)
    observed = gateway.evaluation_measurements[0]
    assert observed["route"] == "report"
    assert observed["input_tokens"] is None
    assert observed["output_tokens"] is None
    assert observed["cost"] is None
    assert observed["usage_status"] == "NOT_MEASURABLE"


async def test_unexpected_response_model_stops_following_calls():
    gateway = ObservedProductionGateway(
        _FakeGateway(SimpleNamespace(model="unexpected", usage=None)),
        allowed_model="test-model",
    )
    with pytest.raises(Exception, match="Unexpected model"):
        await gateway.complete(route=ModelRoute.FAST)
    with pytest.raises(Exception, match="Evaluation stopped"):
        await gateway.complete(route=ModelRoute.FAST)
    assert len(gateway.evaluation_measurements) == 1
    assert gateway.hard_stop_reason == "unexpected_response_model"


def test_study_db_rejects_live_or_existing_database(tmp_path, monkeypatch):
    # Avoid nesting another artifact tree under a long Windows pytest path.
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    with pytest.raises(ValueError, match="new local SQLite"):
        _investigation_database_url("postgresql+asyncpg://live.invalid/live")
    with pytest.raises(ValueError, match="must be new"):
        _investigation_database_url(f"sqlite+aiosqlite:///{Path.cwd() / 'data/business.db'}")
    url = _investigation_database_url("")
    target = Path(url.removeprefix("sqlite+aiosqlite:///"))
    target.write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="must be new"):
        _investigation_database_url(url)
