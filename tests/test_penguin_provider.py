from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch

from walletquota.models import Account
from walletquota.providers.penguinpay.config import PenguinPaySettings
from walletquota.providers.penguinpay.provider import PenguinPayProvider, _collect
from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.client import APIError, AuthenticationError
from walletquota.providers.signed_pool.pool import Order, PoolDataError, PoolSnapshot


def complete_snapshot(complete=True):
    now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    return PoolSnapshot(
        orders=(
            Order(
                order_no="PP-1",
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
            Account("penguinpay", "bound_upi", "upi-1", upi="local-test@upi"),
        ),
        warnings=(),
        bound_upi_total=1,
        bank_card_total=0,
        claimed_task_total=0,
    )


class PenguinPayProviderTests(TestCase):
    def test_maps_complete_snapshot_with_penguin_identity_and_defaults(self):
        captured = []
        provider = PenguinPayProvider(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"},
            collect=lambda settings: captured.append(settings)
            or (complete_snapshot(), account_snapshot()),
        )

        result = provider.run()

        self.assertEqual(
            (provider.name, result.provider, result.status),
            ("penguinpay", "penguinpay", "success"),
        )
        self.assertEqual(
            (captured[0].sort_type, captured[0].order_type, captured[0].page_size),
            (1, 1, 10),
        )
        self.assertEqual(result.orders[0].provider, "penguinpay")
        self.assertEqual(result.accounts[0].provider, "penguinpay")

    def test_incomplete_snapshot_is_partial_and_errors_are_redacted(self):
        incomplete = PenguinPayProvider(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"},
            collect=lambda settings: complete_snapshot(False),
        ).run()
        self.assertEqual(incomplete.status, "partial")

        cases = (
            (AuthenticationError("raw-auth"), "authentication"),
            (APIError("raw-api"), "api"),
            (PoolDataError("raw-data"), "data"),
        )
        for error, category in cases:
            provider = PenguinPayProvider(
                {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"},
                collect=lambda settings, raised=error: (_ for _ in ()).throw(raised),
            )
            result = provider.run()
            self.assertEqual(result.error.category, category)
            self.assertNotIn("raw-", result.error.message)

    def test_missing_credentials_is_a_channel_configuration_error(self):
        result = PenguinPayProvider(
            {}, collect=lambda settings: complete_snapshot()
        ).run()
        self.assertEqual(result.error.category, "configuration")

    def test_collect_logs_in_then_reads_pool_and_all_account_sources(self):
        settings = PenguinPaySettings.from_values(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"}
        )
        events = []
        client = Mock()
        with patch(
            "walletquota.providers.penguinpay.provider.PenguinPayClient",
            return_value=client,
        ) as client_factory, patch(
            "walletquota.providers.penguinpay.provider.collect_pool",
            side_effect=lambda selected_client, selected_settings: events.append("pool")
            or complete_snapshot(),
        ) as pool_collector, patch(
            "walletquota.providers.penguinpay.provider.collect_accounts",
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
