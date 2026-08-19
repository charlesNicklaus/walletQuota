from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from walletquota.models import Account
from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.client import APIError, AuthenticationError
from walletquota.providers.signed_pool.pool import Order, PoolDataError, PoolSnapshot
from walletquota.providers.signed_pool.provider import ProviderMessages, SignedPoolProvider
from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


MILLER_SPEC = ChannelSpec(
    name="millerpay",
    display_name="MillerPay",
    api_label="MillerPay",
    api_base="https://api.millerpay-app.com/app",
    web_origin="https://app-web.millerpay-app.com",
    user_agent="WalletQuota/millerpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)

MESSAGES = ProviderMessages(
    configuration="MillerPay configuration is invalid",
    authentication="MillerPay authentication failed",
    api="MillerPay API request failed",
    data="MillerPay returned invalid order data",
    incomplete="MillerPay order-pool snapshot is incomplete",
)


class FakeConfigError(ValueError):
    pass


def pool_snapshot(complete=True):
    now = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
    return PoolSnapshot(
        orders=(
            Order(
                order_no="MP1",
                order_amount=Decimal("522"),
                income=Decimal("18.66"),
                balance=Decimal("540.66"),
                inr_recharge_rate=None,
                inr_recharge_fee=Decimal("3"),
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


def account_snapshot(warnings=()):
    return AccountSnapshot(
        accounts=(
            Account(
                provider="millerpay",
                kind="bound_upi",
                account_id="upi-1",
                upi="alice@paytm",
                attributes={"status": 1},
            ),
        ),
        warnings=tuple(warnings),
        bound_upi_total=1,
        bank_card_total=0,
        claimed_task_total=0,
    )


def make_provider(collect, settings_factory=lambda values, overrides: object()):
    return SignedPoolProvider(
        values={"PHONE": "x", "PASSWORD": "secret"},
        spec=MILLER_SPEC,
        settings_factory=settings_factory,
        config_error=FakeConfigError,
        messages=MESSAGES,
        collect=collect,
    )


class SignedPoolProviderTests(TestCase):
    def test_maps_complete_snapshot_to_current_provider_and_standard_metadata(self):
        provider = make_provider(
            lambda settings: (pool_snapshot(), account_snapshot())
        )

        result = provider.run()

        self.assertEqual(provider.name, "millerpay")
        self.assertEqual(result.provider, "millerpay")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.orders[0].provider, "millerpay")
        self.assertEqual(result.accounts[0].provider, "millerpay")
        self.assertEqual(result.metadata["order_type"], 1)
        self.assertIsNone(result.orders[0].attributes["inr_recharge_rate"])
        self.assertEqual(
            result.orders[0].attributes["inr_recharge_fee"], Decimal("3")
        )
        self.assertIsNone(result.orders[0].attributes["vip"])

    def test_incomplete_snapshot_returns_partial_with_fixed_message(self):
        provider = make_provider(lambda settings: pool_snapshot(complete=False))

        result = provider.run()

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.error.category, "incomplete")
        self.assertEqual(result.error.message, MESSAGES.incomplete)

    def test_expected_failures_use_fixed_categories_and_messages(self):
        cases = (
            (
                lambda: make_provider(
                    lambda settings: pool_snapshot(),
                    settings_factory=lambda values, overrides: (_ for _ in ()).throw(
                        FakeConfigError("configuration secret")
                    ),
                ),
                "configuration",
                MESSAGES.configuration,
            ),
            (
                lambda: make_provider(
                    lambda settings: (_ for _ in ()).throw(
                        AuthenticationError("authentication secret")
                    )
                ),
                "authentication",
                MESSAGES.authentication,
            ),
            (
                lambda: make_provider(
                    lambda settings: (_ for _ in ()).throw(APIError("API secret"))
                ),
                "api",
                MESSAGES.api,
            ),
            (
                lambda: make_provider(
                    lambda settings: (_ for _ in ()).throw(
                        PoolDataError("data secret")
                    )
                ),
                "data",
                MESSAGES.data,
            ),
        )

        for factory, category, message in cases:
            with self.subTest(category=category):
                result = factory().run()

                self.assertEqual(result.error.category, category)
                self.assertEqual(result.error.message, message)
                self.assertNotIn("secret", result.error.message)

    def test_account_warnings_keep_only_fixed_allowlist_and_safe_fallback(self):
        warnings = (
            "MillerPay claimed task page limit reached: raw-page-response",
            "unexpected warning: raw-account-response",
            "another unexpected warning: raw-other-response",
            "MillerPay claimed task page limit reached: duplicate-raw-response",
        )
        provider = make_provider(
            lambda settings: (pool_snapshot(), account_snapshot(warnings))
        )

        result = provider.run()

        self.assertEqual(
            result.metadata["account_warnings"],
            [
                "MillerPay claimed task page limit reached",
                "MillerPay account collection failed",
            ],
        )
        self.assertNotIn("raw-", str(result.metadata))

    def test_pool_only_collection_uses_an_empty_account_snapshot(self):
        provider = make_provider(lambda settings: pool_snapshot())

        result = provider.run()

        self.assertEqual(result.accounts, ())
        self.assertEqual(result.metadata["account_warnings"], [])
