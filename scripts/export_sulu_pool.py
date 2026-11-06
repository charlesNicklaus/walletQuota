"""Build Sulu Pay's per-channel payout-pool summary from a walletQuota report."""

from decimal import Decimal
from pathlib import Path
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_postgres import identify_report, insert
from scripts.merge_wallet_reports import SafeArgumentParser, merge_reports, timestamp
from walletquota.reporting import _atomic_text


# Sulu Pay Enums.PayChannel identifiers. MillerPay is operated as DONEPAY.
PAYOUT_CHANNELS = {
    "atgpay": "AGTPAY",
    "millerpay": "DONEPAY",
    "penguinpay": "PENGUINPAY",
    "sharkpay": "SHARKPAY",
    "smartwallet": "SMARTWALLET",
    "showpay": "SHOWPAY",
    "hoyopay": "HOYOPAY",
}


def _order_types(metadata):
    types = metadata.get("order_types", (metadata.get("order_type"),))
    if not isinstance(types, (list, tuple)) or not types:
        return None
    if any(type(value) is not int or value < 1 for value in types):
        return None
    return ",".join(str(value) for value in sorted(set(types)))


def _observation_time(channel, types, run_finished_at):
    try:
        if types and "," in types:
            # All reported pools must have been observed recently to trust a zero.
            pools = channel.metadata.get("pool_snapshots", ())
            if not isinstance(pools, (tuple, list)) or not all(isinstance(p, Mapping) for p in pools):
                return None
            ids = [str(pool.get("order_type")) for pool in pools]
            if len(ids) != len(set(ids)) or set(ids) != set(types.split(",")):
                return None
            if channel.complete and any(p.get("complete") is not True for p in pools):
                return None
            times = [timestamp(p.get("finished_at")) for p in pools]
        else:
            times = [timestamp(channel.metadata.get("finished_at"))]
    except ValueError:
        return None
    return min(times) if max(times) <= run_finished_at else None


def build_sulu_export(report, run_id):
    rows = []
    for channel in report.channels:
        types = _order_types(channel.metadata)
        failed = channel.status == "error"
        rows.append((
            run_id, report.started_at, channel.provider, PAYOUT_CHANNELS.get(channel.provider),
            _observation_time(channel, types, report.finished_at), types,
            channel.status, channel.complete,
            None if failed else len(channel.orders),
            None if failed else sum((order.amount for order in channel.orders), Decimal(0)),
        ))
    statement = insert("t_payout_pool_snapshot", (
        "run_id", "run_started_at", "provider", "channel", "observed_at", "order_types",
        "status", "complete", "order_count", "order_amount_inr",
    ), rows)
    return "BEGIN;\n" + statement + "COMMIT;\n"


def main():
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output must differ")
    report = merge_reports([args.input])
    run_id, _, _ = identify_report(report)
    sql = build_sulu_export(report, run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(args.output, lambda handle: handle.write(sql))
    print("Sulu Pay: {} channels, run {}".format(len(report.channels), run_id))


if __name__ == "__main__":
    main()
