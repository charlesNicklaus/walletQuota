from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import Order


CENT = Decimal("0.01")


def _sum_optional(
    orders: Sequence[Order], name: str
) -> Tuple[Optional[Decimal], int]:
    values = [
        value
        for value in (getattr(order, name) for order in orders)
        if value is not None
    ]
    if not values:
        return None, 0
    return sum(values, Decimal("0")), len(values)


def _median(amounts: List[Decimal]) -> Optional[Decimal]:
    if not amounts:
        return None
    ordered = sorted(amounts)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


def empty_summary() -> Dict[str, Any]:
    return {
        "order_count": 0,
        "order_amount": Decimal("0"),
        "minimum_amount": None,
        "maximum_amount": None,
        "mean_amount": None,
        "median_amount": None,
        "total_income": None,
        "income_coverage": 0,
        "total_balance": None,
        "balance_coverage": 0,
        "amount_distribution": [],
    }


def summarize_orders(orders: Sequence[Order]) -> Dict[str, Any]:
    orders = tuple(orders)
    if not orders:
        return empty_summary()

    amounts = [order.amount for order in orders]
    order_count = len(amounts)
    order_amount = sum(amounts, Decimal("0"))
    buckets = Counter(amounts)
    distribution = []
    for amount in sorted(buckets):
        count = buckets[amount]
        percentage = (
            Decimal(count) * Decimal("100") / Decimal(order_count)
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        distribution.append(
            {
                "amount": amount,
                "count": count,
                "percentage": percentage,
                "subtotal": amount * count,
            }
        )

    total_income, income_coverage = _sum_optional(orders, "income")
    total_balance, balance_coverage = _sum_optional(orders, "balance")
    return {
        "order_count": order_count,
        "order_amount": order_amount,
        "minimum_amount": min(amounts),
        "maximum_amount": max(amounts),
        "mean_amount": order_amount / Decimal(order_count),
        "median_amount": _median(amounts),
        "total_income": total_income,
        "income_coverage": income_coverage,
        "total_balance": total_balance,
        "balance_coverage": balance_coverage,
        "amount_distribution": distribution,
    }
