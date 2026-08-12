from datetime import datetime
from typing import Sequence

from .models import AggregateResult, ProviderResult
from .summary import summarize_orders


def aggregate_results(
    results: Sequence[ProviderResult],
    started_at: datetime,
    finished_at: datetime,
) -> AggregateResult:
    results = tuple(results)
    if not results:
        raise ValueError("At least one provider result is required")
    if any(result.status == "success" and not result.complete for result in results):
        raise ValueError("A successful provider result must be complete")

    trusted_orders = tuple(
        order
        for result in results
        if result.status == "success" and result.complete
        for order in result.orders
    )
    total = summarize_orders(trusted_orders)
    total.update(
        {
            "channel_success": sum(
                result.status == "success" for result in results
            ),
            "channel_partial": sum(
                result.status == "partial" for result in results
            ),
            "channel_error": sum(result.status == "error" for result in results),
        }
    )
    status = (
        "success"
        if all(result.status == "success" for result in results)
        else "partial"
    )
    return AggregateResult(status, results, total, started_at, finished_at)
