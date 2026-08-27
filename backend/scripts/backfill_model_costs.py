from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.core.config import get_settings
from app.infrastructure.database.engine import Database
from app.infrastructure.database.models import (
    AgentRunRecord,
    ModelCallRecord,
    RunEventRecord,
)
from app.infrastructure.llm.pricing import estimate_deepseek_cost


async def main() -> None:
    database = Database(get_settings().database_url)
    totals: dict[str, float] = defaultdict(float)
    inserted = 0
    try:
        async with database.session_factory() as session:
            events = await session.scalars(
                select(RunEventRecord)
                .where(RunEventRecord.event_type == "model_call_end")
                .order_by(RunEventRecord.id)
            )
            for event in events:
                payload = event.payload
                usage = payload.get("usage")
                if not isinstance(usage, dict):
                    continue
                call_id = str(
                    payload.get("model_call_id")
                    or uuid5(NAMESPACE_URL, f"run-event:{event.id}")
                )
                existing = await session.get(ModelCallRecord, call_id)
                if existing is not None:
                    totals[event.run_id] += existing.estimated_cost
                    continue
                model = str(payload.get("model") or "")
                input_tokens = int(usage.get("input_tokens") or 0)
                cached_input_tokens = int(
                    usage.get("cached_input_tokens") or 0
                )
                output_tokens = int(usage.get("output_tokens") or 0)
                estimate = estimate_deepseek_cost(
                    model=model,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                )
                session.add(
                    ModelCallRecord(
                        id=call_id,
                        run_id=event.run_id,
                        model=model,
                        route=str(payload.get("route") or "fast"),
                        status="completed",
                        input_tokens=input_tokens,
                        cached_input_tokens=cached_input_tokens,
                        output_tokens=output_tokens,
                        estimated_cost=estimate.amount,
                        currency=estimate.currency,
                        pricing_model=estimate.pricing_model,
                        latency_ms=int(payload.get("latency_ms") or 0),
                    )
                )
                totals[event.run_id] += estimate.amount
                inserted += 1

            for run_id, amount in totals.items():
                run = await session.get(AgentRunRecord, run_id)
                if run is not None:
                    run.estimated_cost = round(amount, 8)
            await session.commit()
    finally:
        await database.dispose()

    print(f"Backfilled {inserted} model calls across {len(totals)} runs.")


if __name__ == "__main__":
    asyncio.run(main())
