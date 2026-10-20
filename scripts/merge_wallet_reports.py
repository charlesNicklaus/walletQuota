#!/usr/bin/env python3
"""Merge saved wallet snapshots offline using the standard report format."""

import argparse
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from walletquota.aggregate import aggregate_results
from walletquota.config import PROVIDER_NAME
from walletquota.models import Account, Order, ProviderError, ProviderResult
from walletquota.reporting import format_terminal_report, write_report_bundle


SNAPSHOT_NOTE = "不同采集时间的离线快照合并，非同时快照。"


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "参数错误，请查看 --help。\n")


def mapping(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Expected a timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp timezone is required")
    return parsed


def optional_decimal(value):
    return None if value is None or value == "" else Decimal(str(value))


def load_channel(name, raw, report_metadata):
    if not isinstance(name, str) or not PROVIDER_NAME.fullmatch(name):
        raise ValueError("Invalid provider name")
    raw = mapping(raw)
    status, complete = raw["status"], raw["complete"]
    if status not in {"success", "partial", "error"} or not isinstance(complete, bool):
        raise ValueError("Unsupported snapshot status")
    if not isinstance(raw["orders"], list) or not isinstance(raw["accounts"], list):
        raise ValueError("Expected order and account arrays")

    orders = []
    for item in raw["orders"]:
        values = dict(mapping(item))
        values["amount"] = Decimal(str(values["amount"]))
        for field in ("income", "balance"):
            values[field] = optional_decimal(values.get(field))
        orders.append(Order(**values))
    if len({item.order_id for item in orders}) != len(orders):
        raise ValueError("Duplicate order identifiers")
    accounts = tuple(Account(**mapping(item)) for item in raw["accounts"])
    metadata = dict(mapping(raw["metadata"]))
    if "pools" in raw:
        if not isinstance(raw["pools"], list):
            raise ValueError("Expected pool snapshots")
        if "pool_snapshots" in metadata and metadata["pool_snapshots"] != raw["pools"]:
            raise ValueError("Conflicting pool snapshots")
        metadata["pool_snapshots"] = raw["pools"]
    history = metadata.get("source_reports", [])
    if not isinstance(history, list):
        raise ValueError("Unsupported source report history")
    metadata["source_reports"] = history + [
        {"metadata": report_metadata, "note": SNAPSHOT_NOTE}
    ]

    error = None
    if status != "success":
        if "error" in raw or status == "error":
            supplied_error = mapping(raw["error"])
            if any(not isinstance(supplied_error.get(field), str)
                   for field in ("category", "message")):
                raise ValueError("Invalid saved snapshot error")
        # Hoyo partial snapshots have no error object; generate a safe model error.
        # Saved exception text may contain credentials; use fixed report messages.
        error = ProviderError(
            "incomplete" if status == "partial" else "saved_snapshot",
            "Saved snapshot is incomplete" if status == "partial"
            else "Saved snapshot reported an error",
        )
    return ProviderResult(name, status, tuple(orders), complete, metadata, error, accounts)


def merge_reports(paths):
    channels, started, finished, seen = [], [], [], set()
    for path in paths:
        with Path(path).open(encoding="utf-8") as handle:
            source = mapping(json.load(handle))
        metadata = mapping(source["metadata"])
        start, finish = timestamp(metadata["started_at"]), timestamp(metadata["finished_at"])
        if start > finish:
            raise ValueError("Collection timestamps are reversed")
        started.append(start)
        finished.append(finish)
        if "channels" in source:
            snapshots = mapping(source["channels"])
        elif source.get("provider") == "hoyopay":
            snapshots = {source["provider"]: source}
        else:
            raise ValueError("Unsupported report format")
        if not snapshots:
            raise ValueError("No channel snapshots")
        for name, raw in snapshots.items():
            if name in seen:
                raise ValueError("Duplicate provider snapshots")
            seen.add(name)
            channels.append(load_channel(name, raw, metadata))
    return aggregate_results(tuple(channels), min(started), max(finished))


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Saved aggregate or Hoyo JSON")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    try:
        result = merge_reports(args.inputs)
        bundle = write_report_bundle(result, args.output_dir)
        print(format_terminal_report(result))
        print("\n{}采集时间跨度: {} 至 {}".format(
            SNAPSHOT_NOTE, result.started_at.isoformat(), result.finished_at.isoformat()
        ))
        print("报告: {}".format(bundle.aggregate_json.resolve()))
    except Exception as exc:
        print("合并失败（{}）：输入无效或无法写入报告。".format(type(exc).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
