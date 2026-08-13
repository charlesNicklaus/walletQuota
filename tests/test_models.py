from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from walletquota.models import (
    Account,
    AggregateResult,
    Order,
    ProviderError,
    ProviderResult,
)


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

    def test_order_attributes_are_deeply_immutable_copies(self):
        attributes = {"details": {"tags": ["first"], "flags": {"new"}}}
        order = Order("atgpay", "A1", Decimal("10"), attributes=attributes)

        attributes["details"]["tags"].append("source-change")
        attributes["details"]["flags"].add("source-change")

        self.assertEqual(order.attributes["details"]["tags"], ("first",))
        self.assertEqual(order.attributes["details"]["flags"], frozenset({"new"}))
        with self.assertRaises(TypeError):
            order.attributes["details"]["new"] = "result-change"
        with self.assertRaises(AttributeError):
            order.attributes["details"]["tags"].append("result-change")
        with self.assertRaises(AttributeError):
            order.attributes["details"]["flags"].add("result-change")


class AccountModelTests(TestCase):
    def test_account_accepts_supported_kinds_with_an_account_or_upi(self):
        for kind, account, upi in (
            ("bound_upi", None, "alice123@paytm"),
            ("bank_account", "123456789012", None),
            ("order_payee_upi", None, "bob@example"),
            ("order_payee_bank", "987654321098", None),
        ):
            value = Account("atgpay", kind, "safe-id", account=account, upi=upi)
            self.assertEqual(value.kind, kind)

    def test_account_rejects_unsupported_kind_or_missing_identifier(self):
        with self.assertRaises(ValueError):
            Account("atgpay", "wallet", "safe-id", account="123456789012")
        with self.assertRaises(ValueError):
            Account("atgpay", "bound_upi", "safe-id")

    def test_account_requires_identifier_that_matches_its_kind(self):
        with self.assertRaises(ValueError):
            Account("atgpay", "bound_upi", "safe-id", account="123456789012")
        with self.assertRaises(ValueError):
            Account("atgpay", "order_payee_upi", "safe-id", account="123456789012")
        with self.assertRaises(ValueError):
            Account("atgpay", "bank_account", "safe-id", upi="alice123@paytm")
        with self.assertRaises(ValueError):
            Account("atgpay", "order_payee_bank", "safe-id", upi="alice123@paytm")

    def test_account_attributes_are_immutable_copies(self):
        attributes = {"enabled": True}
        account = Account(
            "atgpay", "bound_upi", "safe-id", upi="alice123@paytm", attributes=attributes
        )
        attributes["enabled"] = False

        self.assertTrue(account.attributes["enabled"])
        with self.assertRaises(TypeError):
            account.attributes["enabled"] = False

    def test_account_attributes_are_deeply_immutable_copies(self):
        attributes = {"details": {"labels": ["kept"]}}
        account = Account(
            "atgpay", "bound_upi", "safe-id", upi="alice123@paytm", attributes=attributes
        )

        attributes["details"]["labels"].append("source-change")

        self.assertEqual(account.attributes["details"]["labels"], ("kept",))
        with self.assertRaises(TypeError):
            account.attributes["details"]["labels"] = ()


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

    def test_metadata_is_a_deeply_immutable_copy(self):
        metadata = {"responses": [{"state": "kept"}]}
        result = ProviderResult.success("atgpay", (), metadata)

        metadata["responses"][0]["state"] = "source-change"

        self.assertEqual(result.metadata["responses"][0]["state"], "kept")
        with self.assertRaises(TypeError):
            result.metadata["responses"][0]["state"] = "result-change"

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

    def test_result_rejects_accounts_from_another_provider(self):
        account = Account("xxxpay", "bound_upi", "safe-id", upi="alice123@paytm")
        with self.assertRaises(ValueError):
            ProviderResult.success("atgpay", (), {}, (account,))

    def test_failure_results_have_empty_accounts(self):
        failure = ProviderResult.failure("atgpay", "api", "Request failed")

        self.assertEqual(failure.accounts, ())

    def test_error_results_reject_accounts(self):
        account = Account("atgpay", "bound_upi", "safe-id", upi="alice123@paytm")
        with self.assertRaises(ValueError):
            ProviderResult(
                "atgpay",
                "error",
                (),
                False,
                {},
                ProviderError("api", "Request failed"),
                (account,),
            )


class AggregateResultTests(TestCase):
    def test_aggregate_copies_total_mapping(self):
        now = datetime.now(timezone.utc)
        total = {"order_count": 0}
        result = AggregateResult("success", (), total, now, now)
        total["order_count"] = 1

        self.assertEqual(result.total["order_count"], 0)
        with self.assertRaises(TypeError):
            result.total["order_count"] = 2
