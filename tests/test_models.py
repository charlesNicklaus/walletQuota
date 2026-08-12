from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from walletquota.models import AggregateResult, Order, ProviderResult


class OrderModelTests(TestCase):
    def test_order_requires_provider_and_order_id(self):
        with self.assertRaises(ValueError):
            Order("", "A1", Decimal("10"))
        with self.assertRaises(ValueError):
            Order("atgpay", "", Decimal("10"))

    def test_order_requires_inr_and_finite_money(self):
        with self.assertRaises(ValueError):
            Order("atgpay", "A1", Decimal("NaN"))
        with self.assertRaises(ValueError):
            Order("atgpay", "A1", Decimal("10"), income=Decimal("Infinity"))
        with self.assertRaises(ValueError):
            Order("atgpay", "A1", Decimal("10"), currency="USD")

    def test_order_attributes_are_immutable_copies(self):
        attributes = {"vip": 0}
        order = Order("atgpay", "A1", Decimal("10"), attributes=attributes)
        attributes["vip"] = 1

        self.assertEqual(order.attributes["vip"], 0)
        with self.assertRaises(TypeError):
            order.attributes["vip"] = 2


class ProviderResultTests(TestCase):
    def test_success_result_is_complete_and_copies_metadata(self):
        metadata = {"pages": 1}
        order = Order("atgpay", "A1", Decimal("10"))
        result = ProviderResult.success("atgpay", (order,), metadata)
        metadata["pages"] = 2

        self.assertEqual(result.status, "success")
        self.assertTrue(result.complete)
        self.assertEqual(result.metadata["pages"], 1)
        with self.assertRaises(TypeError):
            result.metadata["pages"] = 3

    def test_partial_and_failure_have_safe_error_models(self):
        order = Order("atgpay", "A1", Decimal("10"))
        partial = ProviderResult.partial(
            "atgpay", (order,), {"pages": 1}, "Snapshot incomplete"
        )
        failure = ProviderResult.failure(
            "xxxpay", "authentication", "Authentication failed"
        )

        self.assertEqual(partial.status, "partial")
        self.assertFalse(partial.complete)
        self.assertEqual(partial.error.category, "incomplete")
        self.assertEqual(failure.orders, ())
        self.assertEqual(failure.error.message, "Authentication failed")

    def test_result_rejects_orders_from_another_provider(self):
        order = Order("xxxpay", "A1", Decimal("10"))
        with self.assertRaises(ValueError):
            ProviderResult.success("atgpay", (order,), {})


class AggregateResultTests(TestCase):
    def test_aggregate_copies_total_mapping(self):
        now = datetime.now(timezone.utc)
        total = {"order_count": 0}
        result = AggregateResult("success", (), total, now, now)
        total["order_count"] = 1

        self.assertEqual(result.total["order_count"], 0)
        with self.assertRaises(TypeError):
            result.total["order_count"] = 2
