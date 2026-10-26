from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch

from walletquota.models import Account
from walletquota.providers.smartwallet.client import SmartWalletClient
from walletquota.providers.smartwallet.config import ConfigError, SmartWalletSettings
from walletquota.providers.smartwallet.provider import SmartWalletProvider, _collect
from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.client import APIError, AuthenticationError, make_signature
from walletquota.providers.signed_pool.pool import Order, PoolDataError, PoolSnapshot


def pool_snapshot(complete=True):
    now = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
    return PoolSnapshot(
        orders=(Order("SW-1", Decimal("522"), None, None, None, None, None),),
        observed_totals=(1 if complete else 2,), pages_fetched=1,
        duplicate_count=0, complete=complete, stable=True,
        stop_reason="reached_total" if complete else "empty_page",
        sort_type=1, order_type=1, started_at=now, finished_at=now,
        inr_today_buy_count=0,
    )


class SmartWalletProviderTests(TestCase):
    values = {"SMARTWALLET_PHONE": "fixture-phone", "SMARTWALLET_PASSWORD": "fixture-secret"}

    def test_observed_login_and_low_amount_contract_use_only_smartwallet_hosts(self):
        transport = Mock()
        transport.post.side_effect = [
            {"code": 200, "data": {"userId": 9, "loginToken": "fixture-token", "sessionKey": "fixture-session"}},
            {"code": 200, "data": {"total": 0, "list": []}},
        ]
        settings = SmartWalletSettings.from_values(self.values)
        self.assertEqual((settings.sort_type, settings.order_type, settings.page_size), (1, 1, 10))
        client = SmartWalletClient(settings, transport=transport, clock_ms=lambda: 100)
        client.login()
        client.fetch_pool_page(1, 10, 1, 1)

        login = transport.post.call_args_list[0].args
        self.assertEqual(login[0], "https://api.smartwallet-app.com/app/auth/login")
        self.assertEqual(login[1], {"phone": "fixture-phone", "password": "fixture-secret"})
        self.assertEqual(login[2]["Origin"], "https://app-web.smartwallet-app.com")
        self.assertNotIn("Signature", login[2])
        pool = transport.post.call_args_list[1].args
        self.assertEqual(pool[0], "https://api.smartwallet-app.com/app/pay/debit/task")
        self.assertEqual(pool[1], {"page": 1, "size": 10, "sortType": 1, "orderType": 1, "ts": 100, "userId": 9})
        self.assertEqual(pool[2]["Signature"], make_signature("fixture-session", pool[1]))
        with self.assertRaises(APIError):
            client._url("/pay/receive/task")
        self.assertEqual(transport.post.call_count, 2)

    def test_config_rejects_foreign_hosts_and_does_not_allow_secret_overrides(self):
        for base in ("https://api.sharkpay-app.com/app", "http://api.smartwallet-app.com/app", "https://api.smartwallet-app.com:8443/app"):
            with self.subTest(base=base), self.assertRaises(ConfigError):
                SmartWalletSettings.from_values({**self.values, "SMARTWALLET_API_BASE": base})
        settings = SmartWalletSettings.from_values(self.values, overrides={
            "password": "ignored-password", "phone": "ignored-phone", "api_base": "https://evil.example/app", "page_size": 25,
        })
        self.assertEqual(settings.password, "fixture-secret")
        self.assertEqual(settings.phone, "fixture-phone")
        self.assertEqual(settings.page_size, 25)
        self.assertNotIn("fixture-secret", repr(settings))

    def test_collection_reads_pool_and_account_sources_under_smartwallet_identity(self):
        settings = SmartWalletSettings.from_values(self.values)
        client = Mock()
        accounts = AccountSnapshot((Account("smartwallet", "bound_upi", "fixture-upi", upi="fixture@upi"),), (), 1, 0, 0)
        events = []
        client.login.side_effect = lambda: events.append("login")
        with patch("walletquota.providers.smartwallet.provider.SmartWalletClient", return_value=client), patch(
            "walletquota.providers.smartwallet.provider.collect_pool", side_effect=lambda *args: events.append("pool") or pool_snapshot()
        ), patch("walletquota.providers.smartwallet.provider.collect_accounts", side_effect=lambda *args: events.append("accounts") or accounts):
            collected = _collect(settings)
        result = SmartWalletProvider(self.values, collect=lambda unused: collected).run()

        self.assertEqual(events, ["login", "pool", "accounts"])
        self.assertEqual((result.provider, result.status), ("smartwallet", "success"))
        self.assertEqual((result.orders[0].provider, result.orders[0].amount), ("smartwallet", Decimal("522")))
        self.assertEqual(result.accounts[0].provider, "smartwallet")
        self.assertEqual(result.metadata["bound_upi_total"], 1)

    def test_configuration_authentication_and_partial_results_remain_channel_local(self):
        self.assertEqual(SmartWalletProvider({}).run().error.category, "configuration")
        for error, category in ((AuthenticationError("RAW_AUTH_SECRET"), "authentication"), (APIError("RAW_AUTH_SECRET"), "api"), (PoolDataError("RAW_AUTH_SECRET"), "data")):
            with self.subTest(category=category):
                result = SmartWalletProvider(self.values, collect=Mock(side_effect=error)).run()
                self.assertEqual(result.error.category, category)
                self.assertNotIn("RAW_AUTH_SECRET", result.error.message)
        result = SmartWalletProvider(self.values, collect=lambda unused: pool_snapshot(False)).run()
        self.assertEqual((result.provider, result.status, len(result.orders)), ("smartwallet", "partial", 1))
