from decimal import Decimal
from unittest import TestCase

from walletquota.models import Order
from walletquota.summary import empty_summary, summarize_orders


class SummaryTests(TestCase):
    def test_summarizes_orders_across_providers(self):
        orders = (
            Order(
                "atgpay",
                "A1",
                Decimal("10"),
                income=Decimal("1"),
                balance=Decimal("11"),
            ),
            Order("xxxpay", "A1", Decimal("20")),
            Order(
                "xxxpay",
                "A2",
                Decimal("20"),
                income=Decimal("2"),
                balance=Decimal("22"),
            ),
        )

        summary = summarize_orders(orders)

        self.assertEqual(summary["order_count"], 3)
        self.assertEqual(summary["order_amount"], Decimal("50"))
        self.assertEqual(summary["minimum_amount"], Decimal("10"))
        self.assertEqual(summary["maximum_amount"], Decimal("20"))
        self.assertEqual(summary["mean_amount"], Decimal("50") / Decimal("3"))
        self.assertEqual(summary["median_amount"], Decimal("20"))
        self.assertEqual(summary["total_income"], Decimal("3"))
        self.assertEqual(summary["income_coverage"], 2)
        self.assertEqual(summary["total_balance"], Decimal("33"))
        self.assertEqual(summary["balance_coverage"], 2)
        self.assertEqual(
            summary["amount_distribution"],
            [
                {
                    "amount": Decimal("10"),
                    "count": 1,
                    "percentage": Decimal("33.33"),
                    "subtotal": Decimal("10"),
                },
                {
                    "amount": Decimal("20"),
                    "count": 2,
                    "percentage": Decimal("66.67"),
                    "subtotal": Decimal("40"),
                },
            ],
        )

    def test_even_order_count_uses_decimal_median(self):
        summary = summarize_orders(
            (
                Order("atgpay", "A1", Decimal("10")),
                Order("atgpay", "A2", Decimal("20")),
            )
        )

        self.assertEqual(summary["median_amount"], Decimal("15"))

    def test_empty_summary_does_not_invent_optional_totals(self):
        summary = summarize_orders(())

        self.assertEqual(summary, empty_summary())
        self.assertEqual(summary["order_count"], 0)
        self.assertEqual(summary["order_amount"], Decimal("0"))
        self.assertIsNone(summary["minimum_amount"])
        self.assertIsNone(summary["total_income"])
        self.assertEqual(summary["amount_distribution"], [])
