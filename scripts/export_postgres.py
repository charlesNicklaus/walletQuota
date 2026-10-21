#!/usr/bin/env python3
"""Export a saved wallet report to a local PostgreSQL import file."""

from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.merge_wallet_reports import SafeArgumentParser, merge_reports
from walletquota.reporting import _atomic_text, _channel_dict, _json_value, _safe_report_mapping


def clean_json(value):
    return _json_value(_safe_report_mapping(value))


def reject_nul(value):
    if isinstance(value, str) and "\x00" in value:
        raise ValueError("PostgreSQL text cannot contain NUL")
    if isinstance(value, dict):
        for key, item in value.items():
            reject_nul(key)
            reject_nul(item)
    elif isinstance(value, list):
        for item in value:
            reject_nul(item)


def quote(value):
    reject_nul(value)
    # Explicit escape strings work even if a client parses the whole file
    # before the session's standard_conforming_strings setting is applied.
    return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return quote(str(value)) + "::numeric"
    if isinstance(value, UUID):
        return quote(str(value)) + "::uuid"
    if isinstance(value, datetime):
        return quote(value.isoformat()) + "::timestamptz"
    if isinstance(value, (dict, list)):
        return quote(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)) + "::jsonb"
    return quote(value)


def insert(table, columns, rows):
    if not rows:
        return ""
    values = ",\n".join("  (" + ", ".join(literal(value) for value in row) + ")" for row in rows)
    return "INSERT INTO public.{} ({}) VALUES\n{}\nON CONFLICT DO NOTHING;\n".format(
        table, ", ".join(columns), values
    )


def build_export(report):
    # The loader preserves each original report's top-level metadata here.
    source_metadata = clean_json(report.channels[0].metadata["source_reports"][-1]["metadata"])
    payload = {
        "metadata": source_metadata,
        "status": report.status,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "channels": {channel.provider: _channel_dict(channel) for channel in report.channels},
    }
    reject_nul(payload)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    run_id = uuid5(NAMESPACE_URL, "walletQuota:report:" + digest)
    chunks = [
        "-- walletQuota PostgreSQL data export; apply sql/schema.postgresql.sql first.\n",
        "-- Run: {}\n".format(run_id),
        "BEGIN;\nSET LOCAL standard_conforming_strings = on;\n",
        insert("wallet_crawl_runs", (
            "run_id", "source_sha256", "started_at", "finished_at", "status", "metadata",
        ), [(run_id, digest, report.started_at, report.finished_at, report.status, source_metadata)]),
    ]
    snapshots, orders, accounts = [], [], []
    for channel in report.channels:
        safe = payload["channels"][channel.provider]
        unknown = channel.status == "error"
        snapshots.append((
            run_id, channel.provider, channel.status, channel.complete,
            None if unknown else len(channel.orders),
            None if unknown else sum((row.amount for row in channel.orders), Decimal("0")),
            safe["summary"], safe["metadata"],
            channel.error.category if channel.error else None,
            channel.error.message if channel.error else None,
        ))
        orders.extend((
            run_id, channel.provider, row.order_id, row.amount, row.currency,
            row.income, row.balance, clean_json(row.attributes),
        ) for row in channel.orders)
        seen_accounts = set()
        for row in channel.accounts:
            key = (row.kind, row.account_id, row.order_id or "")
            if key in seen_accounts:
                raise ValueError("Duplicate account snapshot")
            seen_accounts.add(key)
            accounts.append((
                run_id, channel.provider, row.kind, row.account_id, row.order_id or "",
                row.account, row.upi, row.bank_name, row.ifsc, clean_json(row.attributes),
            ))
    chunks.extend([
        insert("wallet_channel_snapshots", (
            "run_id", "provider", "status", "complete", "order_count", "order_amount",
            "summary", "metadata", "error_category", "error_message",
        ), snapshots),
        insert("wallet_orders", (
            "run_id", "provider", "order_id", "amount", "currency", "income", "balance", "attributes",
        ), orders),
        insert("wallet_accounts", (
            "run_id", "provider", "kind", "account_id", "order_id", "account", "upi", "bank_name", "ifsc", "attributes",
        ), accounts),
        "COMMIT;\n",
    ])
    return run_id, "\n".join(chunks)


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Saved aggregate.json")
    parser.add_argument("--output", type=Path, required=True, help="Local SQL data file (contains account data)")
    args = parser.parse_args(argv)
    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("Output must differ from input")
        report = merge_reports([args.input])
        run_id, sql = build_export(report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        _atomic_text(args.output, lambda handle: handle.write(sql))
    except Exception as error:
        print("导出失败（{}）：报告无效或无法写入。".format(type(error).__name__), file=sys.stderr)
        return 1
    print(json.dumps({
        "run_id": str(run_id), "channel_count": len(report.channels),
        "order_count": sum(len(channel.orders) for channel in report.channels),
        "account_count": sum(len(channel.accounts) for channel in report.channels),
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
