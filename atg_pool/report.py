import csv
import json
import os
import tempfile
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .pool import Order, PoolSnapshot


CENT = Decimal("0.01")
CSV_FIELDS = [
    "order_no",
    "order_amount",
    "income",
    "balance",
    "inr_recharge_rate",
    "inr_recharge_fee",
    "vip",
]


def _sum_optional(
    orders: Tuple[Order, ...], name: str
) -> Tuple[Optional[Decimal], int]:
    values = [
        value for value in (getattr(order, name) for order in orders) if value is not None
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


def build_summary(snapshot: PoolSnapshot) -> Dict[str, Any]:
    amounts = [order.order_amount for order in snapshot.orders]
    total_amount = sum(amounts, Decimal("0"))
    count = len(amounts)
    buckets = Counter(amounts)
    distribution = []
    for amount in sorted(buckets):
        bucket_count = buckets[amount]
        percentage = (
            (Decimal(bucket_count) * Decimal("100") / Decimal(count)).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            if count
            else Decimal("0")
        )
        distribution.append(
            {
                "amount": amount,
                "count": bucket_count,
                "percentage": percentage,
                "subtotal": amount * bucket_count,
            }
        )

    total_income, income_coverage = _sum_optional(snapshot.orders, "income")
    total_balance, balance_coverage = _sum_optional(snapshot.orders, "balance")

    return {
        "api_total": snapshot.latest_total,
        "collected_count": count,
        "total_amount": total_amount,
        "minimum_amount": min(amounts) if amounts else None,
        "maximum_amount": max(amounts) if amounts else None,
        "mean_amount": total_amount / Decimal(count) if count else None,
        "median_amount": _median(amounts),
        "total_income": total_income,
        "income_coverage": income_coverage,
        "total_balance": total_balance,
        "balance_coverage": balance_coverage,
        "amount_distribution": distribution,
    }


def _decimal_text(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_terminal_report(
    snapshot: PoolSnapshot, summary: Mapping[str, Any]
) -> str:
    order_count = int(summary["collected_count"])

    def optional_total(label: str, total_key: str, coverage_key: str) -> str:
        value = summary[total_key]
        coverage = int(summary[coverage_key])
        if value is None:
            return "{}: 不可用".format(label)
        suffix = ""
        if coverage < order_count:
            suffix = "（部分数据 {}/{}）".format(coverage, order_count)
        return "{}: ₹{}{}".format(label, _decimal_text(value), suffix)

    lines = [
        "ATG 订单池报告",
        "=" * 58,
        "快照时间: {} / {} UTC".format(
            snapshot.finished_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            snapshot.finished_at.astimezone(__import__("datetime").timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        ),
        "筛选条件: sortType={}, orderType={}".format(
            snapshot.sort_type, snapshot.order_type
        ),
        "API 订单总数: {}".format(summary["api_total"]),
        "已收集订单数: {}".format(summary["collected_count"]),
        "订单金额合计: ₹{}".format(_decimal_text(summary["total_amount"])),
        "最低 / 最高: ₹{} / ₹{}".format(
            _decimal_text(summary["minimum_amount"]),
            _decimal_text(summary["maximum_amount"]),
        ),
        "平均 / 中位数: ₹{} / ₹{}".format(
            _decimal_text(summary["mean_amount"]),
            _decimal_text(summary["median_amount"]),
        ),
        optional_total("收益合计", "total_income", "income_coverage"),
        optional_total("含收益金额合计", "total_balance", "balance_coverage"),
        "",
        "金额分布",
        "金额            订单数        占比          小计",
        "-" * 58,
    ]
    for bucket in summary["amount_distribution"]:
        lines.append(
            "₹{:<12} {:>7} {:>11}%   ₹{:>12}".format(
                _decimal_text(bucket["amount"]),
                bucket["count"],
                _decimal_text(bucket["percentage"]),
                _decimal_text(bucket["subtotal"]),
            )
        )
    if not summary["amount_distribution"]:
        lines.append("（当前没有订单）")
    lines.extend(["", "完整性: {}".format("完整" if snapshot.complete else "快照不完整")])
    if not snapshot.stable:
        lines.append("警告: 订单池在抓取期间发生变化，分布是跨页快照。")
    if snapshot.duplicate_count:
        lines.append("重复订单号已去重: {}".format(snapshot.duplicate_count))
    return "\n".join(lines)


def _order_dict(order: Order) -> Dict[str, Any]:
    return {
        "order_no": order.order_no,
        "order_amount": _decimal_text(order.order_amount),
        "income": _decimal_text(order.income) if order.income is not None else "",
        "balance": _decimal_text(order.balance) if order.balance is not None else "",
        "inr_recharge_rate": (
            _decimal_text(order.inr_recharge_rate)
            if order.inr_recharge_rate is not None
            else ""
        ),
        "inr_recharge_fee": (
            _decimal_text(order.inr_recharge_fee)
            if order.inr_recharge_fee is not None
            else ""
        ),
        "vip": order.vip,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _atomic_text(path: Path, writer: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}-".format(path.name), dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer(handle)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_reports(
    snapshot: PoolSnapshot, summary: Mapping[str, Any], directory: Path
) -> Tuple[Path, Path]:
    directory = Path(directory)
    local_stamp = snapshot.finished_at.astimezone().strftime("%Y%m%d_%H%M%S")
    status_suffix = "" if snapshot.complete else "_partial"
    base_name = "atg_pool_{}{}".format(local_stamp, status_suffix)
    csv_path = directory / "{}.csv".format(base_name)
    json_path = directory / "{}.json".format(base_name)

    def write_csv(handle: Any) -> None:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(_order_dict(order) for order in snapshot.orders)

    payload = {
        "metadata": {
            "snapshot_local": snapshot.finished_at.astimezone().isoformat(),
            "snapshot_utc": snapshot.finished_at.isoformat(),
            "sort_type": snapshot.sort_type,
            "order_type": snapshot.order_type,
            "pages_fetched": snapshot.pages_fetched,
            "complete": snapshot.complete,
            "stable": snapshot.stable,
            "stop_reason": snapshot.stop_reason,
            "observed_totals": list(snapshot.observed_totals),
            "duplicate_count": snapshot.duplicate_count,
            "inr_today_buy_count": snapshot.inr_today_buy_count,
        },
        "summary": {
            key: _json_value(value)
            for key, value in summary.items()
            if key != "amount_distribution"
        },
        "amount_distribution": _json_value(summary["amount_distribution"]),
        "orders": [_order_dict(order) for order in snapshot.orders],
    }

    _atomic_text(csv_path, write_csv)
    _atomic_text(
        json_path,
        lambda handle: json.dump(
            payload, handle, ensure_ascii=False, indent=2, sort_keys=True
        ),
    )
    return csv_path, json_path
