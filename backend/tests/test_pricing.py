from types import SimpleNamespace

from app.infrastructure.llm.gateway import OpenAICompatibleGateway
from app.infrastructure.llm.pricing import estimate_deepseek_cost


def test_deepseek_v4_flash_cache_aware_cost() -> None:
    estimate = estimate_deepseek_cost(
        model="deepseek-v4-flash",
        input_tokens=1_000_000,
        cached_input_tokens=250_000,
        output_tokens=100_000,
    )

    assert estimate.priced is True
    assert estimate.currency == "CNY"
    # 0.25M cached×0.1 + 0.75M uncached×3 + 0.1M output×9
    assert estimate.amount == 3.175


def test_deprecated_chat_alias_uses_v4_flash_price() -> None:
    estimate = estimate_deepseek_cost(
        model="deepseek-chat",
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=1_000_000,
    )

    assert estimate.pricing_model == "deepseek-v4-flash"
    # 1M uncached×3 + 1M output×9
    assert estimate.amount == 12


def test_version_suffixed_model_alias_prices() -> None:
    # 实际运行 model 名带版本后缀（deepseek-v4-flash-0731），必须命中定价。
    estimate = estimate_deepseek_cost(
        model="deepseek-v4-flash-0731",
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=1_000_000,
    )

    assert estimate.priced is True
    assert estimate.pricing_model == "deepseek-v4-flash"
    assert estimate.amount == 12


def test_gateway_reads_deepseek_cache_usage_fields() -> None:
    usage = SimpleNamespace(
        prompt_tokens=1_000,
        prompt_cache_hit_tokens=600,
        prompt_cache_miss_tokens=400,
        completion_tokens=100,
        total_tokens=1_100,
    )
    message = SimpleNamespace(content="ok", tool_calls=[])
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=message,
                finish_reason="stop",
            )
        ],
        usage=usage,
        model="deepseek-v4-flash",
    )

    converted = OpenAICompatibleGateway._convert_response(response)

    assert converted.usage.cached_input_tokens == 600
    assert converted.usage.uncached_input_tokens == 400
    # 600 cached×0.1/M + 400 uncached×3/M + 100 output×9/M
    assert converted.estimated_cost == 0.00216
    assert converted.pricing_model == "deepseek-v4-flash"


def test_unpriced_model_reports_no_cost() -> None:
    """Any model other than the flash default is flagged unpriced."""
    estimate = estimate_deepseek_cost(
        model="deepseek-v4-pro",
        input_tokens=1_000,
        cached_input_tokens=0,
        output_tokens=100,
    )
    assert estimate.priced is False
    assert estimate.pricing_model is None
    assert estimate.amount == 0
