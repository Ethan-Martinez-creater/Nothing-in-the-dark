from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ModelPrice:
    canonical_model: str
    cached_input_per_million: Decimal
    uncached_input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "CNY"
    source: str = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"


@dataclass(frozen=True, slots=True)
class CostEstimate:
    amount: float
    currency: str
    pricing_model: str | None
    priced: bool


# Only the flash model is used by every API route (fast / reasoning / report);
# any other model name is intentionally unpriced so cost accounting flags it.
DEEPSEEK_PRICES = {
    "deepseek-v4-flash": ModelPrice(
        canonical_model="deepseek-v4-flash",
        cached_input_per_million=Decimal("0.1"),
        uncached_input_per_million=Decimal("3"),
        output_per_million=Decimal("9"),
    ),
}

MODEL_ALIASES = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
    "deepseek-v4-flash-0731": "deepseek-v4-flash",
}


def estimate_deepseek_cost(
    *,
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> CostEstimate:
    normalized = model.strip().lower()
    canonical = MODEL_ALIASES.get(normalized, normalized)
    price = DEEPSEEK_PRICES.get(canonical)
    if price is None:
        return CostEstimate(
            amount=0,
            currency="CNY",
            pricing_model=None,
            priced=False,
        )

    cached = max(0, min(cached_input_tokens, input_tokens))
    uncached = max(0, input_tokens - cached)
    million = Decimal(1_000_000)
    amount = (
        Decimal(cached) * price.cached_input_per_million
        + Decimal(uncached) * price.uncached_input_per_million
        + Decimal(max(0, output_tokens)) * price.output_per_million
    ) / million
    return CostEstimate(
        amount=float(amount.quantize(Decimal("0.00000001"))),
        currency=price.currency,
        pricing_model=price.canonical_model,
        priced=True,
    )
