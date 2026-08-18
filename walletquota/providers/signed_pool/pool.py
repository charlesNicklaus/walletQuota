import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple


class PoolDataError(ValueError):
    """Raised when order-pool data does not match the confirmed contract."""


def _decimal(value: Any, field_name: str, non_negative: bool = False) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PoolDataError("Invalid {} in order data".format(field_name)) from error
    if not decimal_value.is_finite() or (non_negative and decimal_value < 0):
        raise PoolDataError("Invalid {} in order data".format(field_name))
    return decimal_value


def _optional_decimal(value: Any, field_name: str) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    return _decimal(value, field_name)


@dataclass(frozen=True)
class Order:
    order_no: str
    order_amount: Decimal
    income: Optional[Decimal]
    balance: Optional[Decimal]
    inr_recharge_rate: Optional[Decimal]
    inr_recharge_fee: Optional[Decimal]
    vip: Any

    @classmethod
    def from_api(cls, value: Mapping[str, Any]) -> "Order":
        order_no = value.get("orderNo")
        if order_no is None or not str(order_no).strip():
            raise PoolDataError("Order data is missing orderNo")
        if "orderAmount" not in value:
            raise PoolDataError("Order data is missing orderAmount")
        return cls(
            order_no=str(order_no),
            order_amount=_decimal(value["orderAmount"], "orderAmount", non_negative=True),
            income=_optional_decimal(value.get("income"), "income"),
            balance=_optional_decimal(value.get("balance"), "balance"),
            inr_recharge_rate=_optional_decimal(
                value.get("inrRechargeRate"), "inrRechargeRate"
            ),
            inr_recharge_fee=_optional_decimal(
                value.get("inrRechargeFee"), "inrRechargeFee"
            ),
            vip=value.get("vip"),
        )


@dataclass(frozen=True)
class PoolSnapshot:
    orders: Tuple[Order, ...]
    observed_totals: Tuple[int, ...]
    pages_fetched: int
    duplicate_count: int
    complete: bool
    stable: bool
    stop_reason: str
    sort_type: int
    order_type: int
    started_at: datetime
    finished_at: datetime
    inr_today_buy_count: Optional[int] = None

    @property
    def latest_total(self) -> int:
        return self.observed_totals[-1] if self.observed_totals else 0


def _parse_total(data: Mapping[str, Any]) -> int:
    try:
        total = int(data.get("total"))
    except (TypeError, ValueError) as error:
        raise PoolDataError("Pool response contains an invalid total") from error
    if total < 0:
        raise PoolDataError("Pool response total cannot be negative")
    return total


def _parse_list(data: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    rows = data.get("list")
    if not isinstance(rows, list):
        raise PoolDataError("Pool response is missing list")
    if not all(isinstance(row, Mapping) for row in rows):
        raise PoolDataError("Pool response contains an invalid order")
    return rows


def collect_pool(
    client: Any,
    settings: Any,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_pages: int = 10000,
) -> PoolSnapshot:
    started_at = now()
    seen = set()
    orders: List[Order] = []
    totals: List[int] = []
    duplicate_count = 0
    pages_fetched = 0
    stop_reason = "page_limit"
    inr_today_buy_count: Optional[int] = None

    for page in range(1, max_pages + 1):
        data = client.fetch_pool_page(
            page,
            settings.page_size,
            settings.sort_type,
            settings.order_type,
        )
        if not isinstance(data, Mapping):
            raise PoolDataError("Pool response data is invalid")
        total = _parse_total(data)
        rows = _parse_list(data)
        totals.append(total)
        pages_fetched += 1
        if data.get("inrTodayBuyCount") is not None:
            try:
                inr_today_buy_count = int(data["inrTodayBuyCount"])
            except (TypeError, ValueError):
                inr_today_buy_count = None

        for row in rows:
            order = Order.from_api(row)
            if order.order_no in seen:
                duplicate_count += 1
                continue
            seen.add(order.order_no)
            orders.append(order)

        if len(orders) >= total:
            stop_reason = "reached_total"
            break
        if not rows:
            stop_reason = "empty_page"
            break
        if len(rows) < settings.page_size:
            stop_reason = "short_page"
            break
        if page < max_pages and settings.request_delay_ms:
            sleep(settings.request_delay_ms / 1000.0)

    latest_total = totals[-1] if totals else 0
    return PoolSnapshot(
        orders=tuple(orders),
        observed_totals=tuple(totals),
        pages_fetched=pages_fetched,
        duplicate_count=duplicate_count,
        complete=len(orders) == latest_total,
        stable=len(set(totals)) <= 1,
        stop_reason=stop_reason,
        sort_type=settings.sort_type,
        order_type=settings.order_type,
        started_at=started_at,
        finished_at=now(),
        inr_today_buy_count=inr_today_buy_count,
    )


__all__ = ["Order", "PoolDataError", "PoolSnapshot", "collect_pool"]
