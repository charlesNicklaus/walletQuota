import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from atg_pool.pool import Order, PoolSnapshot
from atg_pool.report import build_summary, format_terminal_report, write_reports


def order(order_no, amount, income, balance):
    return Order(
        order_no=order_no,
        order_amount=Decimal(amount),
        income=Decimal(income),
        balance=Decimal(balance),
        inr_recharge_rate=Decimal("3"),
        inr_recharge_fee=Decimal("3"),
        vip=0,
    )


def snapshot(orders, total=None, complete=True, stable=True):
    stamp = datetime(2026, 8, 12, 7, 37, 9, tzinfo=timezone.utc)
    expected = len(orders) if total is None else total
    return PoolSnapshot(
        orders=tuple(orders),
        observed_totals=(expected,),
        pages_fetched=1,
        duplicate_count=0,
        complete=complete,
        stable=stable,
        stop_reason="reached_total" if complete else "short_page",
        sort_type=1,
        order_type=4,
        started_at=stamp,
        finished_at=stamp,
        inr_today_buy_count=0,
    )


class ReportSummaryTests(unittest.TestCase):
    def test_calculates_decimal_metrics_and_exact_distribution(self):
        value = snapshot(
            [
                order("one", "522", "18.66", "540.66"),
                order("two", "522", "18.66", "540.66"),
                order("three", "534", "19.02", "553.02"),
            ]
        )

        summary = build_summary(value)

        self.assertEqual(summary["api_total"], 3)
        self.assertEqual(summary["collected_count"], 3)
        self.assertEqual(summary["total_amount"], Decimal("1578"))
        self.assertEqual(summary["minimum_amount"], Decimal("522"))
        self.assertEqual(summary["maximum_amount"], Decimal("534"))
        self.assertEqual(summary["mean_amount"], Decimal("526"))
        self.assertEqual(summary["median_amount"], Decimal("522"))
        self.assertEqual(summary["total_income"], Decimal("56.34"))
        self.assertEqual(summary["total_balance"], Decimal("1634.34"))
        self.assertEqual(
            summary["amount_distribution"],
            [
                {
                    "amount": Decimal("522"),
                    "count": 2,
                    "percentage": Decimal("66.67"),
                    "subtotal": Decimal("1044"),
                },
                {
                    "amount": Decimal("534"),
                    "count": 1,
                    "percentage": Decimal("33.33"),
                    "subtotal": Decimal("534"),
                },
            ],
        )

    def test_empty_pool_has_zero_totals_and_no_range(self):
        summary = build_summary(snapshot([]))

        self.assertEqual(summary["total_amount"], Decimal("0"))
        self.assertIsNone(summary["minimum_amount"])
        self.assertIsNone(summary["median_amount"])
        self.assertIsNone(summary["total_income"])
        self.assertEqual(summary["income_coverage"], 0)
        self.assertEqual(summary["amount_distribution"], [])

    def test_optional_totals_expose_partial_coverage(self):
        first = order("one", "522", "18.66", "540.66")
        second = Order(
            order_no="two",
            order_amount=Decimal("530"),
            income=None,
            balance=None,
            inr_recharge_rate=Decimal("3"),
            inr_recharge_fee=Decimal("3"),
            vip=0,
        )

        summary = build_summary(snapshot([first, second]))

        self.assertEqual(summary["total_income"], Decimal("18.66"))
        self.assertEqual(summary["income_coverage"], 1)
        self.assertEqual(summary["balance_coverage"], 1)

        text = format_terminal_report(snapshot([first, second]), summary)
        self.assertIn("部分数据 1/2", text)

    def test_missing_optional_totals_render_as_unavailable(self):
        missing = Order(
            order_no="one",
            order_amount=Decimal("522"),
            income=None,
            balance=None,
            inr_recharge_rate=None,
            inr_recharge_fee=None,
            vip=0,
        )
        value = snapshot([missing])

        text = format_terminal_report(value, build_summary(value))

        self.assertIn("收益合计: 不可用", text)
        self.assertIn("含收益金额合计: 不可用", text)

    def test_terminal_report_includes_count_amounts_and_warning(self):
        value = snapshot(
            [order("one", "522", "18.66", "540.66")],
            total=2,
            complete=False,
            stable=False,
        )

        text = format_terminal_report(value, build_summary(value))

        self.assertIn("API 订单总数: 2", text)
        self.assertIn("已收集订单数: 1", text)
        self.assertIn("订单金额合计: ₹522", text)
        self.assertIn("₹522", text)
        self.assertIn("快照不完整", text)
        self.assertIn("抓取期间发生变化", text)


class ReportFileTests(unittest.TestCase):
    def test_writes_deterministic_csv_and_secret_free_json(self):
        value = snapshot(
            [
                order("one", "522", "18.66", "540.66"),
                order("two", "534", "19.02", "553.02"),
            ]
        )
        summary = build_summary(value)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)

        csv_path, json_path = write_reports(value, summary, Path(temp_dir.name))

        self.assertTrue(csv_path.name.startswith("atg_pool_20260812_153709"))
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            list(rows[0]),
            [
                "order_no",
                "order_amount",
                "income",
                "balance",
                "inr_recharge_rate",
                "inr_recharge_fee",
                "vip",
            ],
        )
        self.assertEqual(rows[0]["order_amount"], "522")

        raw_json = json_path.read_text(encoding="utf-8")
        payload = json.loads(raw_json)
        self.assertEqual(payload["summary"]["total_amount"], "1056")
        self.assertEqual(payload["amount_distribution"][0]["amount"], "522")
        self.assertEqual(len(payload["orders"]), 2)
        for secret_name in ("password", "loginToken", "sessionKey", "signature"):
            self.assertNotIn(secret_name, raw_json)

    def test_incomplete_reports_are_marked_partial_in_both_filenames(self):
        value = snapshot(
            [order("one", "522", "18.66", "540.66")],
            total=2,
            complete=False,
        )
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)

        csv_path, json_path = write_reports(
            value, build_summary(value), Path(temp_dir.name)
        )

        self.assertIn("_partial", csv_path.stem)
        self.assertIn("_partial", json_path.stem)


if __name__ == "__main__":
    unittest.main()
