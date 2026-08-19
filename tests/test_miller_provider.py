from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch

from walletquota.models import Account
from walletquota.providers.millerpay.config import MillerPaySettings
from walletquota.providers.millerpay.provider import MillerPayProvider, _collect
from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.client import APIError, AuthenticationError
from walletquota.providers.signed_pool.pool import Order, PoolDataError, PoolSnapshot


def complete_snapshot(complete=True):
    now = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
    return PoolSnapshot(
        orders=(
            Order(
                order_no="MP-1",
                order_amount=Decimal("522"),
                income=Decimal("18.66"),
                balance=Decimal("540.66"),
                inr_recharge_rate=Decimal("3"),
                inr_recharge_fee=None,
                vip=None,
            ),
        ),
        observed_totals=(1 if complete else 2,),
        pages_fetched=1,
        duplicate_count=0,
        complete=complete,
        stable=True,
        stop_reason="reached_total" if complete else "empty_page",
        sort_type=1,
        order_type=1,
        started_at=now,
        finished_at=now,
        inr_today_buy_count=0,
    )


def account_snapshot():
    return AccountSnapshot(
        accounts=(
            Account("millerpay", "bound_upi", "upi-1", upi="local-test@upi"),
        ),
        warnings=(),
        bound_upi_total=1,
        bank_card_total=0,
        claimed_task_total=0,
    )


class MillerPayProviderTests(TestCase):
    def test_maps_complete_snapshot_and_uses_confirmed_defaults(self):
        captured = []
        provider = MillerPayProvider(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"},
            collect=lambda settings: captured.append(settings)
            or (complete_snapshot(), account_snapshot()),
        )

        result = provider.run()

        self.assertEqual(
            (provider.name, result.provider, result.status),
            ("millerpay", "millerpay", "success"),
        )
        self.assertEqual((captured[0].sort_type, captured[0].order_type), (1, 1))
        self.assertEqual(result.orders[0].provider, "millerpay")
        self.assertIsNone(result.orders[0].attributes["inr_recharge_fee"])
        self.assertIsNone(result.orders[0].attributes["vip"])
        self.assertEqual(result.accounts[0].provider, "millerpay")

    def test_incomplete_snapshot_remains_partial(self):
        provider = MillerPayProvider(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"},
            collect=lambda settings: complete_snapshot(False),
        )

        self.assertEqual(provider.run().status, "partial")

    def test_expected_errors_are_fixed_and_do_not_leak_raw_text(self):
        cases = (
            (AuthenticationError("raw-auth"), "authentication"),
            (APIError("raw-api"), "api"),
            (PoolDataError("raw-data"), "data"),
        )
        for error, category in cases:
            with self.subTest(category=category):
                provider = MillerPayProvider(
                    {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"},
                    collect=lambda settings, raised=error: (_ for _ in ()).throw(raised),
                )

                result = provider.run()

                self.assertEqual(result.error.category, category)
                self.assertNotIn("raw-", result.error.message)

    def test_missing_credentials_is_configuration_error(self):
        result = MillerPayProvider(
            {}, collect=lambda settings: complete_snapshot()
        ).run()

        self.assertEqual(result.error.category, "configuration")

    def test_collect_logs_in_then_collects_pool_and_all_account_sources(self):
        settings = MillerPaySettings.from_values(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"}
        )
        events = []
        client = Mock()

        with patch(
            "walletquota.providers.millerpay.provider.MillerPayClient",
            return_value=client,
        ) as client_factory, patch(
            "walletquota.providers.millerpay.provider.collect_pool",
            side_effect=lambda selected_client, selected_settings: events.append("pool")
            or complete_snapshot(),
        ) as pool_collector, patch(
            "walletquota.providers.millerpay.provider.collect_accounts",
            side_effect=lambda selected_client, spec: events.append("accounts")
            or account_snapshot(),
        ) as account_collector:
            client.login.side_effect = lambda: events.append("login")
            result = _collect(settings)

        self.assertEqual(events, ["login", "pool", "accounts"])
        self.assertEqual(result, (complete_snapshot(), account_snapshot()))
        client_factory.assert_called_once_with(settings)
        pool_collector.assert_called_once_with(client, settings)
        account_collector.assert_called_once()
