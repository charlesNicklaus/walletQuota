#!/usr/bin/env python3
"""Read both HOYO PAY order pools and account information for an existing user.

Authentication responses remain in memory. This script never registers users,
sends SMS, receives orders, makes payments, or changes account settings.
"""
import argparse
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from walletquota.config import ConfigError, read_env
from walletquota.models import Order
from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.client import SignedPoolClient
from walletquota.providers.signed_pool.pool import Order as PoolOrder, collect_pool
from walletquota.providers.signed_pool.spec import READ_ONLY_PATHS
from walletquota.reporting import _safe_report_mapping
from walletquota.summary import summarize_orders


API_BASE = "https://api.hoyopay-app.com/app"


def endpoint(configured, path):
    if configured != API_BASE or path not in READ_ONLY_PATHS:
        raise ValueError("Unsupported HOYO PAY endpoint")
    return API_BASE + path


SPEC = SimpleNamespace(
    name="hoyopay",
    api_label="HOYO PAY",
    web_origin="https://app-web.hoyopay-app.com",
    user_agent="WalletQuota/hoyopay-readonly/1.0",
    url_for=endpoint,
)


@dataclass(frozen=True)
class Settings:
    phone: str = field(repr=False)
    password: str = field(repr=False)
    api_base: str = field(default=API_BASE, init=False)
    timeout_seconds: float = 30
    page_size: int = 100
    sort_type: int = 1
    order_type: int = 1
    request_delay_ms: int = 200

    def __post_init__(self):
        if not self.phone.strip() or not self.password:
            raise ConfigError("HOYO PAY credentials are required")


def serial(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "items"):
        return {key: serial(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(item) for item in value]
    return value


def record(value):
    return serial(_safe_report_mapping({
        item.name: getattr(value, item.name) for item in fields(value)
    }))


class _PoolAttempt:
    """Observe duplicate data before the shared collector removes repeated IDs."""

    def __init__(self, client):
        self.client = client
        self.orders = {}
        self.conflicting_order_ids = set()

    def fetch_pool_page(self, page, size, sort_type, order_type):
        data = self.client.fetch_pool_page(page, size, sort_type, order_type)
        if isinstance(data, Mapping) and isinstance(data.get("list"), list):
            for row in data["list"]:
                if not isinstance(row, Mapping):
                    continue  # The shared collector reports malformed rows.
                order = PoolOrder.from_api(row)
                if order.order_no in self.orders:
                    if self.orders[order.order_no] != order:
                        self.conflicting_order_ids.add(order.order_no)
                else:
                    self.orders[order.order_no] = order
        return data


def collect(settings, client):
    client.login()
    snapshots = {}
    attempts = {}
    for order_type in (1, 2):
        selected = replace(settings, order_type=order_type)
        attempt = _PoolAttempt(client)
        snapshot = collect_pool(attempt, selected)
        if (
            snapshot.stop_reason == "short_page"
            and snapshot.pages_fetched == 1
            and 0 < len(snapshot.orders) < snapshot.latest_total
        ):
            # A first-page cap can be followed using the actual supported size.
            attempt = _PoolAttempt(client)
            snapshot = collect_pool(
                attempt, replace(selected, page_size=len(snapshot.orders))
            )
        snapshots[order_type] = snapshot
        attempts[order_type] = attempt
    accounts = collect_accounts(client, SPEC)
    sources = {}
    cross_pool_duplicates = 0
    same_pool_conflicting_orders = {
        order_id
        for attempt in attempts.values()
        for order_id in attempt.conflicting_order_ids
    }
    conflicting_orders = set(same_pool_conflicting_orders)
    per_pool = []
    for order_type, snapshot in snapshots.items():
        pool_orders = []
        for item in snapshot.orders:
            if item.order_no in sources:
                cross_pool_duplicates += 1
                if sources[item.order_no] != item:
                    conflicting_orders.add(item.order_no)
            else:
                sources[item.order_no] = item
            pool_orders.append(Order(
                provider="hoyopay", order_id=item.order_no,
                amount=item.order_amount, income=item.income, balance=item.balance,
            ))
        per_pool.append({
            "order_type": order_type,
            "name": "Low Amount" if order_type == 1 else "High Amount",
            "complete": snapshot.complete and not attempts[order_type].conflicting_order_ids,
            "conflicting_order_count": len(attempts[order_type].conflicting_order_ids),
            "summary": serial(summarize_orders(tuple(pool_orders))),
            "pages_fetched": snapshot.pages_fetched,
            "observed_totals": list(snapshot.observed_totals),
            "duplicate_count": snapshot.duplicate_count,
            "stable": snapshot.stable,
            "stop_reason": snapshot.stop_reason,
            "started_at": snapshot.started_at.isoformat(),
            "finished_at": snapshot.finished_at.isoformat(),
        })
    orders = tuple(
        Order(
            provider="hoyopay", order_id=item.order_no,
            amount=item.order_amount, income=item.income, balance=item.balance,
            attributes={
                "inr_recharge_rate": item.inr_recharge_rate,
                "inr_recharge_fee": item.inr_recharge_fee,
                "vip": item.vip,
            },
        )
        for item in sources.values()
    )
    complete = all(item.complete for item in snapshots.values()) and not conflicting_orders
    return {
        "provider": "hoyopay",
        "status": "success" if complete else "partial",
        "complete": complete,
        "summary": serial(summarize_orders(orders)),
        "pools": per_pool,
        "metadata": {
            "started_at": min(item.started_at for item in snapshots.values()).isoformat(),
            "finished_at": max(item.finished_at for item in snapshots.values()).isoformat(),
            "pages_fetched": sum(item.pages_fetched for item in snapshots.values()),
            "duplicate_count": sum(item.duplicate_count for item in snapshots.values()),
            "cross_pool_duplicate_count": cross_pool_duplicates,
            "same_pool_conflicting_order_count": len(same_pool_conflicting_orders),
            "conflicting_order_count": len(conflicting_orders),
            "stable": all(item.stable for item in snapshots.values()),
            "sort_type": settings.sort_type,
            "order_types": [1, 2],
            "bound_upi_total": accounts.bound_upi_total,
            "bank_card_total": accounts.bank_card_total,
            "claimed_task_total": accounts.claimed_task_total,
            "account_warnings": list(accounts.warnings),
        },
        "orders": [record(item) for item in orders],
        "accounts": [record(item) for item in accounts.accounts],
    }


def write_result(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}-".format(path.name), dir=str(path.parent)
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main(argv=None, stdout=sys.stdout, stderr=sys.stderr):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("reports/hoyopay-result.json"))
    args = parser.parse_args(argv)
    try:
        values = read_env(args.env_file)
        settings = Settings(
            phone=values.get("HOYOPAY_PHONE", ""),
            password=values.get("HOYOPAY_PASSWORD", ""),
        )
        result = collect(settings, SignedPoolClient(settings, SPEC))
        write_result(args.output, result)
    except Exception as error:
        # Server messages and traces may contain authentication data.
        print(json.dumps({
            "provider": "hoyopay", "status": "error",
            "error_type": type(error).__name__,
        }), file=stderr)
        return 1
    print(json.dumps({
        "provider": "hoyopay", "status": result["status"],
        "order_count": result["summary"]["order_count"],
        "order_amount": result["summary"]["order_amount"],
        "amount_distribution": result["summary"]["amount_distribution"],
        "account_count": len(result["accounts"]),
        "pages_fetched": result["metadata"]["pages_fetched"],
        "pools": [{
            "order_type": item["order_type"], "name": item["name"],
            "order_count": item["summary"]["order_count"],
            "order_amount": item["summary"]["order_amount"],
            "complete": item["complete"], "pages_fetched": item["pages_fetched"],
        } for item in result["pools"]],
        "output": str(args.output.resolve()),
    }, ensure_ascii=False), file=stdout)
    return 0 if result["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
