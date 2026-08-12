from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from walletquota.aggregate import aggregate_results
from walletquota.models import Order, ProviderResult


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)


class AggregateTests(TestCase):
    def test_only_complete_successful_channels_enter_trusted_total(self):
        success = ProviderResult.success(
            "atgpay",
            (
                Order("atgpay", "SAME", Decimal("10")),
                Order("atgpay", "A2", Decimal("20")),
            ),
            {},
        )
        partial = ProviderResult.partial(
            "xxxpay",
            (Order("xxxpay", "SAME", Decimal("999")),),
            {},
            "Snapshot incomplete",
        )
        error = ProviderResult.failure("badpay", "api", "API failed")

        result = aggregate_results((success, partial, error), NOW, NOW)

        self.assertEqual(result.status, "partial")
        self.assertEqual(
            [channel.provider for channel in result.channels],
            ["atgpay", "xxxpay", "badpay"],
        )
        self.assertEqual(result.total["channel_success"], 1)
        self.assertEqual(result.total["channel_partial"], 1)
        self.assertEqual(result.total["channel_error"], 1)
        self.assertEqual(result.total["order_count"], 2)
        self.assertEqual(result.total["order_amount"], Decimal("30"))

    def test_all_successful_channels_are_combined(self):
        results = (
            ProviderResult.success(
                "atgpay", (Order("atgpay", "SAME", Decimal("10")),), {}
            ),
            ProviderResult.success(
                "xxxpay", (Order("xxxpay", "SAME", Decimal("20")),), {}
            ),
        )

        result = aggregate_results(results, NOW, NOW)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.total["order_count"], 2)
        self.assertEqual(result.total["order_amount"], Decimal("30"))
        self.assertEqual(result.total["channel_success"], 2)

    def test_empty_result_sequence_is_rejected(self):
        with self.assertRaises(ValueError):
            aggregate_results((), NOW, NOW)

    def test_inconsistent_success_state_is_not_trusted(self):
        inconsistent = object.__new__(ProviderResult)
        object.__setattr__(inconsistent, "provider", "badpay")
        object.__setattr__(inconsistent, "status", "success")
        object.__setattr__(inconsistent, "orders", ())
        object.__setattr__(inconsistent, "complete", False)
        object.__setattr__(inconsistent, "metadata", {})
        object.__setattr__(inconsistent, "error", None)

        with self.assertRaises(ValueError):
            aggregate_results((inconsistent,), NOW, NOW)
