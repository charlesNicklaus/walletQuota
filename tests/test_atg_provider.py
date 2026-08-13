from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from atg_pool.client import APIError, AuthenticationError
from atg_pool.pool import Order as AtgOrder
from atg_pool.pool import PoolDataError, PoolSnapshot
from walletquota.models import Account
from walletquota.providers.atgpay.accounts import AccountSnapshot
from walletquota.providers.atgpay.provider import AtgPayProvider


def complete_snapshot(complete=True):
    now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
    order = AtgOrder(
        order_no="AP1",
        order_amount=Decimal("522"),
        income=Decimal("18.66"),
        balance=Decimal("540.66"),
        inr_recharge_rate=Decimal("3"),
        inr_recharge_fee=Decimal("3"),
        vip=0,
    )
    return PoolSnapshot(
        orders=(order,),
        observed_totals=(1 if complete else 2,),
        pages_fetched=1,
        duplicate_count=0,
        complete=complete,
        stable=True,
        stop_reason="reached_total" if complete else "empty_page",
        sort_type=1,
        order_type=4,
        started_at=now,
        finished_at=now,
        inr_today_buy_count=0,
    )


def account_snapshot(warnings=()):
    return AccountSnapshot(
        (
            Account(
                "atgpay",
                "bound_upi",
                "upi-1",
                upi="alice@paytm",
                attributes={"status": 1},
            ),
        ),
        tuple(warnings),
        1,
        0,
        0,
    )


class AtgPayProviderTests(TestCase):
    def test_returns_pool_orders_and_best_effort_accounts_together(self):
        provider = AtgPayProvider(
            {"ATGPAY_PHONE": "phone", "ATGPAY_PASSWORD": "password"},
            collect=lambda settings: (complete_snapshot(), account_snapshot()),
        )

        result = provider.run()

        self.assertEqual(result.status, "success")
        self.assertEqual([order.order_id for order in result.orders], ["AP1"])
        self.assertEqual([account.upi for account in result.accounts], ["alice@paytm"])
        self.assertEqual(result.metadata["bound_upi_total"], 1)
        self.assertEqual(result.metadata["bank_card_total"], 0)
        self.assertEqual(result.metadata["claimed_task_total"], 0)
        self.assertEqual(result.metadata["account_warnings"], [])

    def test_account_collection_warning_keeps_a_complete_pool_success_and_is_safe(self):
        provider = AtgPayProvider(
            {"ATGPAY_PHONE": "phone", "ATGPAY_PASSWORD": "password"},
            collect=lambda settings: (
                complete_snapshot(),
                account_snapshot(("ATG account failed: raw-secret-response",)),
            ),
        )

        result = provider.run()

        self.assertEqual(result.status, "success")
        self.assertEqual(result.metadata["account_warnings"], ["ATG account failed"])
        self.assertNotIn("raw-secret-response", str(result.metadata))

    def test_legacy_pool_only_collect_seam_receives_an_empty_account_snapshot(self):
        provider = AtgPayProvider(
            {"ATGPAY_PHONE": "phone", "ATGPAY_PASSWORD": "password"},
            collect=lambda settings: complete_snapshot(),
        )

        result = provider.run()

        self.assertEqual(result.status, "success")
        self.assertEqual(result.accounts, ())
        self.assertEqual(result.metadata["account_warnings"], [])

    def test_maps_complete_atg_snapshot_to_standard_result(self):
        provider = AtgPayProvider(
            {"ATGPAY_PHONE": "9000000000", "ATGPAY_PASSWORD": "secret"},
            collect=lambda settings: complete_snapshot(),
        )

        result = provider.run()

        self.assertEqual(provider.name, "atgpay")
        self.assertEqual(result.provider, "atgpay")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.orders[0].provider, "atgpay")
        self.assertEqual(result.orders[0].order_id, "AP1")
        self.assertEqual(result.orders[0].currency, "INR")
        self.assertEqual(result.orders[0].amount, Decimal("522"))
        self.assertEqual(result.orders[0].attributes["vip"], 0)
        self.assertEqual(result.metadata["pages_fetched"], 1)

    def test_new_configuration_names_override_legacy_names(self):
        captured = []
        provider = AtgPayProvider(
            {
                "ATGPAY_PHONE": "new-phone",
                "ATGPAY_PASSWORD": "new-password",
                "ATGPAY_PAGE_SIZE": "25",
                "ATG_PHONE": "old-phone",
                "ATG_PASSWORD": "old-password",
                "ATG_PAGE_SIZE": "10",
            },
            collect=lambda settings: captured.append(settings) or complete_snapshot(),
        )

        provider.run()

        self.assertEqual(captured[0].phone, "new-phone")
        self.assertEqual(captured[0].password, "new-password")
        self.assertEqual(captured[0].page_size, 25)

    def test_legacy_configuration_names_still_work(self):
        provider = AtgPayProvider(
            {"ATG_PHONE": "old-phone", "ATG_PASSWORD": "old-password"},
            collect=lambda settings: complete_snapshot(),
        )

        self.assertEqual(provider.run().status, "success")

    def test_non_secret_overrides_replace_configuration(self):
        captured = []
        provider = AtgPayProvider(
            {"ATGPAY_PHONE": "phone", "ATGPAY_PASSWORD": "password"},
            overrides={"page_size": 50, "sort_type": 2},
            collect=lambda settings: captured.append(settings) or complete_snapshot(),
        )

        provider.run()

        self.assertEqual(captured[0].page_size, 50)
        self.assertEqual(captured[0].sort_type, 2)

    def test_partial_snapshot_is_not_promoted_to_success(self):
        provider = AtgPayProvider(
            {"ATGPAY_PHONE": "phone", "ATGPAY_PASSWORD": "password"},
            collect=lambda settings: complete_snapshot(complete=False),
        )

        result = provider.run()

        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.error.category, "incomplete")

    def test_missing_credentials_return_configuration_error(self):
        result = AtgPayProvider({}, collect=lambda settings: complete_snapshot()).run()

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.category, "configuration")

    def test_expected_failures_are_safely_categorized(self):
        cases = (
            (AuthenticationError("server secret"), "authentication"),
            (APIError("server secret"), "api"),
            (PoolDataError("server secret"), "data"),
        )
        for error, category in cases:
            with self.subTest(category=category):
                provider = AtgPayProvider(
                    {"ATGPAY_PHONE": "phone", "ATGPAY_PASSWORD": "password"},
                    collect=lambda settings, error=error: (_ for _ in ()).throw(error),
                )

                result = provider.run()

                self.assertEqual(result.error.category, category)
                self.assertNotIn("server secret", result.error.message)

    def test_settings_repr_does_not_expose_password(self):
        provider = AtgPayProvider(
            {
                "ATGPAY_PHONE": "phone",
                "ATGPAY_PASSWORD": "do-not-print-password",
            },
            collect=lambda settings: complete_snapshot(),
        )
        provider.run()

        self.assertNotIn("do-not-print-password", repr(provider.settings))
