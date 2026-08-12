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
                    "key": "below_100",
                    "label": "<100",
                    "minimum": None,
                    "maximum": Decimal("100"),
                    "minimum_inclusive": False,
                    "maximum_inclusive": False,
                    "count": 3,
                    "percentage": Decimal("100.00"),
                    "subtotal": Decimal("50"),
                },
                {
                    "key": "100_10000",
                    "label": "100-10000",
                    "minimum": Decimal("100"),
                    "maximum": Decimal("10000"),
                    "minimum_inclusive": True,
                    "maximum_inclusive": False,
                    "count": 0,
                    "percentage": Decimal("0.00"),
                    "subtotal": Decimal("0"),
                },
                {
                    "key": "10000_20000",
                    "label": "10000-20000",
                    "minimum": Decimal("10000"),
                    "maximum": Decimal("20000"),
                    "minimum_inclusive": True,
                    "maximum_inclusive": False,
                    "count": 0,
                    "percentage": Decimal("0.00"),
                    "subtotal": Decimal("0"),
                },
                {
                    "key": "20000_50000",
                    "label": "20000-50000",
                    "minimum": Decimal("20000"),
                    "maximum": Decimal("50000"),
                    "minimum_inclusive": True,
                    "maximum_inclusive": True,
                    "count": 0,
                    "percentage": Decimal("0.00"),
                    "subtotal": Decimal("0"),
                },
                {
                    "key": "above_50000",
                    "label": ">50000",
                    "minimum": Decimal("50000"),
                    "maximum": None,
                    "minimum_inclusive": False,
                    "maximum_inclusive": False,
                    "count": 0,
                    "percentage": Decimal("0.00"),
                    "subtotal": Decimal("0"),
                },
            ],
        )

    def test_amount_distribution_uses_fixed_ranges_at_boundaries(self):
        amounts = (
            Decimal("99.99"),
            Decimal("100"),
            Decimal("9999.99"),
            Decimal("10000"),
            Decimal("19999.99"),
            Decimal("20000"),
            Decimal("50000"),
            Decimal("50000.01"),
        )

        summary = summarize_orders(
            tuple(Order("atgpay", str(index), amount) for index, amount in enumerate(amounts))
        )

        self.assertEqual(
            [row["key"] for row in summary["amount_distribution"]],
            ["below_100", "100_10000", "10000_20000", "20000_50000", "above_50000"],
        )
        self.assertEqual(
            [row["count"] for row in summary["amount_distribution"]], [1, 2, 2, 2, 1]
        )
        self.assertEqual(
            sum((row["subtotal"] for row in summary["amount_distribution"]), Decimal("0")),
            summary["order_amount"],
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
        self.assertEqual(len(summary["amount_distribution"]), 5)
        self.assertEqual(
            [(row["count"], row["percentage"], row["subtotal"])
            for row in summary["amount_distribution"]],
            [(0, Decimal("0.00"), Decimal("0"))] * 5,
        )
