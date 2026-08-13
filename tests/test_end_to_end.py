import json
import unittest
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from atg_pool.config import Settings
from atg_pool.pool import collect_pool
from atg_pool.report import build_summary
from walletquota.aggregate import aggregate_results
from walletquota.models import Account, Order, ProviderResult
from walletquota.reporting import format_terminal_report, write_report_bundle


class FixtureClient:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = 0

    def fetch_pool_page(self, page, size, sort_type, order_type):
        self.calls += 1
        if self.calls == 1:
            return self.fixture["data"]
        return {
            "total": self.fixture["data"]["total"],
            "list": [],
            "inrBuyConfig": "",
            "inrTodayBuyCount": 0,
        }


class ConfirmedResponseContractTests(unittest.TestCase):
    def test_confirmed_299_order_response_is_interpreted_correctly(self):
        fixture_path = Path(__file__).parent / "fixtures" / "pool_page_1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        client = FixtureClient(fixture)
        settings = Settings(
            phone="9876543210",
            password="secret",
            page_size=10,
            request_delay_ms=0,
        )

        snapshot = collect_pool(client, settings, sleep=lambda _: None)
        summary = build_summary(snapshot)

        self.assertEqual(snapshot.latest_total, 299)
        self.assertEqual(summary["collected_count"], 10)
        self.assertFalse(snapshot.complete)
        self.assertEqual(
            [(str(item["amount"]), item["count"]) for item in summary["amount_distribution"]],
            [("522", 8), ("530", 1), ("534", 1)],
        )


class ComprehensiveOutputTests(unittest.TestCase):
    def test_comprehensive_output_has_ranges_masked_terminal_accounts_and_full_json_accounts(self):
        orders = tuple(
            Order("atgpay", "A{}".format(index), amount)
            for index, amount in enumerate(
                (
                    Decimal("99.99"),
                    Decimal("100"),
                    Decimal("10000"),
                    Decimal("20000"),
                    Decimal("50000.01"),
                ),
                start=1,
            )
        )
        upi = "alice123@paytm"
        account_number = "123456789012"
        result = ProviderResult.success(
            "atgpay",
            orders,
            {},
            (
                Account("atgpay", "bound_upi", "upi-1", upi=upi),
                Account(
                    "atgpay",
                    "bank_account",
                    "bank-1",
                    account=account_number,
                    bank_name="HDFC",
                ),
            ),
        )
        now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
        aggregate = aggregate_results((result,), now, now)

        terminal = format_terminal_report(aggregate)
        expected_ranges = [
            "<100",
            "100-10000",
            "10000-20000",
            "20000-50000",
            ">50000",
        ]
        for label in expected_ranges:
            self.assertEqual(terminal.count("范围 {} × 1".format(label)), 2)
        self.assertNotIn(upi, terminal)
        self.assertNotIn(account_number, terminal)
        self.assertIn("a***3@p***m", terminal)
        self.assertIn("****9012", terminal)

        with tempfile.TemporaryDirectory() as temporary:
            bundle = write_report_bundle(aggregate, Path(temporary))
            payload = json.loads(bundle.aggregate_json.read_text(encoding="utf-8"))
            csv_text = bundle.channel_csv["atgpay"].read_text(encoding="utf-8")

        accounts = payload["channels"]["atgpay"]["accounts"]
        self.assertEqual([account["upi"] for account in accounts], [upi, None])
        self.assertEqual(
            [account["account"] for account in accounts], [None, account_number]
        )
        self.assertNotIn(upi, csv_text)
        self.assertNotIn(account_number, csv_text)


if __name__ == "__main__":
    unittest.main()
