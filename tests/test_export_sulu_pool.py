from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase
from uuid import UUID

from scripts.export_postgres import build_export
from scripts.export_sulu_pool import build_sulu_export
from scripts.merge_wallet_reports import merge_reports
from tests.test_export_postgres import report
from walletquota.aggregate import aggregate_results
from walletquota.models import Order, ProviderResult


START = datetime(2026, 1, 2, 1, tzinfo=timezone.utc)
FINISH = "2026-01-02T01:01:00+00:00"
OBSERVED = "E'{}'::timestamptz".format(FINISH)
RUN_ID = UUID("02d9d300-42da-4712-962b-04d2465c53fc")


def export(*channels):
    result = aggregate_results(channels, START, datetime.fromisoformat(FINISH))
    return build_sulu_export(result, RUN_ID)


class ExportSuluPoolTests(TestCase):
    def test_mapping_and_success_partial_error_have_distinct_counts_and_amounts(self):
        meta = {"order_type": 1, "finished_at": FINISH}
        amount = Decimal("123.456789012345")
        sql = export(
            ProviderResult.success("atgpay", (), {**meta, "order_type": 4}),
            ProviderResult.partial("millerpay", (Order("millerpay", "private-id", amount),),
                                   meta, "private-error"),
            ProviderResult.failure("showpay", "api", "private-error"),
        )
        self.assertIn(f"E'atgpay', E'AGTPAY', {OBSERVED}, E'4', E'success', TRUE, 0, E'0'::numeric", sql)
        self.assertIn(f"E'millerpay', E'DONEPAY', {OBSERVED}, E'1', E'partial', FALSE, 1, E'{amount}'::numeric", sql)
        self.assertIn("E'showpay', E'SHOWPAY', NULL, NULL, E'error', FALSE, NULL, NULL", sql)
        self.assertNotIn("private-error", sql)
        self.assertNotIn("private-id", sql)

    def test_zero_without_observation_or_valid_pool_scope_keeps_missing_evidence_null(self):
        sql = export(
            ProviderResult.success("showpay", (), {"order_type": 2}),
            ProviderResult.success("millerpay", (), {"order_types": [True], "finished_at": FINISH}),
        )
        self.assertIn("E'SHOWPAY', NULL, E'2', E'success', TRUE, 0, E'0'::numeric", sql)
        self.assertIn(f"E'DONEPAY', {OBSERVED}, NULL, E'success', TRUE, 0, E'0'::numeric", sql)

    def test_hoyo_uses_union_and_oldest_confirmed_pool_time_or_null_if_missing(self):
        first = "2026-01-02T01:00:10+00:00"
        pools = [{"order_type": 2, "finished_at": FINISH, "complete": True},
                 {"order_type": 1, "finished_at": first, "complete": True}]
        for snapshots, expected in (
            (pools, f"E'{first}'::timestamptz"),
            (pools[:1], "NULL"),
            (pools + pools[:1], "NULL"),
            ([pools[0], dict(pools[1], finished_at="2030-01-01T00:00:00Z")], "NULL"),
            ([pools[0], dict(pools[1], complete=False)], "NULL"),
        ):
            with self.subTest(snapshots=snapshots):
                sql = export(ProviderResult.success("hoyopay", (), {
                    "order_types": [2, 1, 1], "pool_snapshots": snapshots, "finished_at": FINISH,
                }))
                self.assertIn(f"E'HOYOPAY', {expected}, E'1,2', E'success', TRUE, 0", sql)

    def test_saved_report_replay_uses_local_id_and_exports_only_channel_summary(self):
        source = report()
        channel = source["channels"]["fakepay"]
        channel["metadata"]["private_note"] = "private-channel-note"
        channel["orders"].append(dict(channel["orders"][0], order_id="private-order-2", amount="0.0000000018"))
        channel["accounts"].append({
            "provider": "fakepay", "kind": "bank_account", "account_id": "bank-row-id",
            "account": "fictional-bank-number", "bank_name": "private-bank", "ifsc": "private-ifsc",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aggregate.json"
            path.write_text(json.dumps(source))
            expected_run_id, _ = build_export(merge_reports([path]))
            output = Path(directory) / "summary.sql"
            command = [sys.executable, "-m", "scripts.export_sulu_pool", str(path), "--output", str(output)]
            copies = []
            for _ in range(2):
                process = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                copies.append(output.read_text())
        sql, replay = copies
        self.assertEqual(sql, replay)
        self.assertIn(str(expected_run_id), sql)
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertIn("E'fakepay', NULL, NULL, NULL, E'success', TRUE, 2, E'123.4567890141'::numeric", sql)
        for private in ("fictional-order", "private-order-2", "fixture@fictional", "fictional-account",
                        "bank-row-id", "fictional-bank-number", "private-bank", "private-ifsc",
                        "private-channel-note", "private_note", "pages_fetched", "FIXTURE_SECRET",
                        "123.4567890123", "0.0000000018", "wallet_accounts", "wallet_orders"):
            with self.subTest(private=private):
                self.assertNotIn(private, sql)
