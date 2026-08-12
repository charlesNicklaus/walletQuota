from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import Order


CENT = Decimal("0.01")
AMOUNT_BUCKETS = (
    ("below_100", "<100", None, Decimal("100"), False, False),
    ("100_10000", "100-10000", Decimal("100"), Decimal("10000"), True, False),
    ("10000_20000", "10000-20000", Decimal("10000"), Decimal("20000"), True, False),
    ("20000_50000", "20000-50000", Decimal("20000"), Decimal("50000"), True, True),
    ("above_50000", ">50000", Decimal("50000"), None, False, False),
)


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


def _amount_distribution(orders: Sequence[Order]) -> List[Dict[str, Any]]:
    rows = []
    order_count = len(orders)
    for key, label, minimum, maximum, minimum_inclusive, maximum_inclusive in AMOUNT_BUCKETS:
        amounts = [
            order.amount
            for order in orders
            if (minimum is None or order.amount > minimum or (minimum_inclusive and order.amount == minimum))
            and (maximum is None or order.amount < maximum or (maximum_inclusive and order.amount == maximum))
        ]
        count = len(amounts)
        percentage = Decimal("0.00")
        if order_count:
            percentage = (
                Decimal(count) * Decimal("100") / Decimal(order_count)
            ).quantize(CENT, rounding=ROUND_HALF_UP)
        rows.append(
            {
                "key": key,
                "label": label,
                "minimum": minimum,
                "maximum": maximum,
                "minimum_inclusive": minimum_inclusive,
                "maximum_inclusive": maximum_inclusive,
                "count": count,
                "percentage": percentage,
                "subtotal": sum(amounts, Decimal("0")),
            }
        )
    return rows


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
        "amount_distribution": _amount_distribution(()),
    }


def summarize_orders(orders: Sequence[Order]) -> Dict[str, Any]:
    orders = tuple(orders)
    if not orders:
        return empty_summary()

    amounts = [order.amount for order in orders]
    order_count = len(amounts)
    order_amount = sum(amounts, Decimal("0"))
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
        "amount_distribution": _amount_distribution(orders),
    }
