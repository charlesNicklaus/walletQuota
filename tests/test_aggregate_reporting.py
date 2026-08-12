import csv
import json
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from walletquota.aggregate import aggregate_results
from walletquota.models import Order, ProviderResult
from walletquota.reporting import format_terminal_report, write_report_bundle


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
SENTINEL = "DO_NOT_PERSIST_SECRET_7f31"


def aggregate_fixture():
    success = ProviderResult.success(
        "atgpay",
        (
            Order(
                "atgpay",
                "AP1",
                Decimal("522"),
                income=Decimal("18.66"),
                balance=Decimal("540.66"),
                attributes={"vip": 0, "rate": Decimal("3")},
            ),
            Order("atgpay", "AP2", Decimal("530")),
        ),
        {"pages_fetched": 1, "stable": True},
    )
    partial = ProviderResult.partial(
        "xxxpay",
        (Order("xxxpay", "XP1", Decimal("999")),),
        {"pages_fetched": 2},
        "Snapshot incomplete",
    )
    error = ProviderResult.failure(
        "badpay", "authentication", "Authentication failed"
    )
    return aggregate_results((success, partial, error), NOW, NOW)


class TerminalReportTests(TestCase):
    def test_formats_total_then_every_channel_in_stable_order(self):
        report = format_terminal_report(aggregate_fixture())

        self.assertTrue(report.startswith("订单池综合报告"))
        self.assertLess(report.index("total:"), report.index("atgpay: success"))
        self.assertLess(report.index("atgpay: success"), report.index("xxxpay: partial"))
        self.assertLess(report.index("xxxpay: partial"), report.index("badpay: error"))
        self.assertIn("渠道: 成功 1 / 部分 1 / 失败 1", report)
        self.assertIn("订单数: 2", report)
        self.assertIn("总金额: ₹1052", report)
        self.assertIn("范围 100-10000 × 2（100%，小计 ₹1052）", report)
        self.assertIn("范围 <100 × 0（0%，小计 ₹0）", report)
        self.assertIn("订单数: 1（不计入 total）", report)
        self.assertIn("错误: authentication / Authentication failed", report)


class ReportBundleTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_writes_aggregate_json_and_nonempty_channel_csv_files(self):
        bundle = write_report_bundle(aggregate_fixture(), self.directory)

        self.assertEqual(bundle.directory.name, "20260812_160000")
        self.assertEqual(bundle.aggregate_json.name, "aggregate.json")
        self.assertEqual(set(bundle.channel_csv), {"atgpay", "xxxpay"})
        self.assertTrue(bundle.aggregate_json.is_file())
        self.assertTrue(bundle.channel_csv["atgpay"].is_file())
        self.assertNotIn("badpay", bundle.channel_csv)

        payload = json.loads(bundle.aggregate_json.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"metadata", "total", "channels"})
        self.assertEqual(list(payload["channels"]), ["atgpay", "xxxpay", "badpay"])
        self.assertEqual(payload["total"]["order_count"], 2)
        self.assertEqual(payload["total"]["order_amount"], "1052")
        self.assertEqual(payload["channels"]["xxxpay"]["status"], "partial")
        self.assertEqual(payload["channels"]["badpay"]["orders"], [])

        with bundle.channel_csv["atgpay"].open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["order_id"] for row in rows], ["AP1", "AP2"])
        self.assertEqual(rows[0]["amount"], "522")
        self.assertEqual(json.loads(rows[0]["attributes"]), {"rate": "3", "vip": 0})

    def test_same_timestamp_uses_numbered_directory_without_overwrite(self):
        first = write_report_bundle(aggregate_fixture(), self.directory)
        second = write_report_bundle(aggregate_fixture(), self.directory)

        self.assertEqual(first.directory.name, "20260812_160000")
        self.assertEqual(second.directory.name, "20260812_160000_2")
        self.assertTrue(first.aggregate_json.is_file())
        self.assertTrue(second.aggregate_json.is_file())

    def test_report_does_not_persist_unrelated_process_secrets(self):
        result = aggregate_fixture()
        bundle = write_report_bundle(result, self.directory)

        for path in bundle.directory.iterdir():
            self.assertNotIn(SENTINEL, path.read_text(encoding="utf-8"))
            self.assertNotIn("sessionKey", path.read_text(encoding="utf-8"))
            self.assertNotIn("loginToken", path.read_text(encoding="utf-8"))
