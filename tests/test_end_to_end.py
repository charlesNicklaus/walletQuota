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
from walletquota.summary import summarize_orders


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
    def test_two_channels_keep_full_accounts_local_and_remove_nested_secrets(self):
        secret_values = {
            "password": "miller-password-sentinel",
            "loginToken": "miller-login-token-sentinel",
            "sessionKey": "miller-session-key-sentinel",
            "cookie": "miller-cookie-sentinel",
            "Signature": "miller-signature-sentinel",
            "authorization": "miller-authorization-sentinel",
        }
        sensitive_attributes = {
            "password": secret_values["password"],
            "nested": {
                "loginToken": secret_values["loginToken"],
                "items": [
                    {"sessionKey": secret_values["sessionKey"]},
                    {"cookie": secret_values["cookie"]},
                    {"Signature": secret_values["Signature"]},
                    {"authorization": secret_values["authorization"]},
                ],
            },
        }
        full_upi = "miller.collector@upi"
        full_account_number = "123456789012"
        full_ifsc = "HDFC0000123"
        atgpay = ProviderResult.success(
            "atgpay",
            (Order("atgpay", "ATG-1", Decimal("1")),),
            {},
        )
        millerpay = ProviderResult.success(
            "millerpay",
            tuple(
                Order(
                    "millerpay",
                    "MILLER-{}".format(index),
                    amount,
                    attributes=sensitive_attributes,
                )
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
            ),
            {"nested": sensitive_attributes},
            (
                Account(
                    "millerpay",
                    "bound_upi",
                    "miller-upi-1",
                    upi=full_upi,
                    attributes=sensitive_attributes,
                ),
                Account(
                    "millerpay",
                    "bank_account",
                    "miller-bank-1",
                    account=full_account_number,
                    bank_name="HDFC",
                    ifsc=full_ifsc,
                    attributes=sensitive_attributes,
                ),
            ),
        )
        now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
        aggregate = aggregate_results((atgpay, millerpay), now, now)

        terminal = format_terminal_report(aggregate)
        expected_ranges = [
            ("<100", 1),
            ("100-10000", 1),
            ("10000-20000", 1),
            ("20000-50000", 1),
            (">50000", 1),
        ]
        self.assertEqual(
            [
                (row["label"], row["count"])
                for row in summarize_orders(millerpay.orders)["amount_distribution"]
            ],
            expected_ranges,
        )
        self.assertIn("atgpay: success", terminal)
        self.assertIn("millerpay: success", terminal)
        self.assertNotIn(full_upi, terminal)
        self.assertNotIn(full_account_number, terminal)
        self.assertNotIn(full_ifsc, terminal)

        with tempfile.TemporaryDirectory() as temporary:
            bundle = write_report_bundle(aggregate, Path(temporary))
            aggregate_text = bundle.aggregate_json.read_text(encoding="utf-8")
            payload = json.loads(aggregate_text)
            csv_texts = {
                provider: path.read_text(encoding="utf-8")
                for provider, path in bundle.channel_csv.items()
            }

        self.assertEqual(set(bundle.channel_csv), {"atgpay", "millerpay"})
        self.assertEqual(
            payload["channels"]["millerpay"]["accounts"][0]["upi"], full_upi
        )
        self.assertEqual(
            payload["channels"]["millerpay"]["accounts"][1]["account"],
            full_account_number,
        )
        self.assertEqual(
            payload["channels"]["millerpay"]["accounts"][1]["ifsc"],
            full_ifsc,
        )
        self.assertNotIn(full_upi, csv_texts["millerpay"])
        self.assertNotIn(full_account_number, csv_texts["millerpay"])
        self.assertNotIn(full_ifsc, csv_texts["millerpay"])
        for secret in secret_values.values():
            self.assertNotIn(secret, terminal)
            self.assertNotIn(secret, aggregate_text)
            for csv_text in csv_texts.values():
                self.assertNotIn(secret, csv_text)

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
        ifsc = "HDFC0000123"
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
                    ifsc=ifsc,
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
        self.assertNotIn(ifsc, terminal)
        self.assertIn("a***3@p***m", terminal)
        self.assertIn("****9012", terminal)
        self.assertIn("IFSC ****0123", terminal)

        with tempfile.TemporaryDirectory() as temporary:
            bundle = write_report_bundle(aggregate, Path(temporary))
            payload = json.loads(bundle.aggregate_json.read_text(encoding="utf-8"))
            csv_text = bundle.channel_csv["atgpay"].read_text(encoding="utf-8")

        accounts = payload["channels"]["atgpay"]["accounts"]
        self.assertEqual([account["upi"] for account in accounts], [upi, None])
        self.assertEqual(
            [account["account"] for account in accounts], [None, account_number]
        )
        self.assertEqual([account["ifsc"] for account in accounts], [None, ifsc])
        self.assertNotIn(upi, csv_text)
        self.assertNotIn(account_number, csv_text)
        self.assertNotIn(ifsc, csv_text)


if __name__ == "__main__":
    unittest.main()
