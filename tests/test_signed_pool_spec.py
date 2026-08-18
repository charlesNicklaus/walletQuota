from dataclasses import FrozenInstanceError, replace
from unittest import TestCase

from walletquota.providers.signed_pool.spec import (
    READ_ONLY_PATHS,
    ChannelSpec,
)


def spec():
    return ChannelSpec(
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


class ChannelSpecTests(TestCase):
    def test_exact_read_only_allowlist_and_url_resolution(self):
        channel = spec()
        self.assertEqual(
            channel.allowed_paths,
            frozenset(
                {
                    "/auth/login",
                    "/pay/debit/task",
                    "/user/upi/bind/list",
                    "/bank/debit/card/list",
                    "/pay/my/task",
                    "/pay/order/detail",
                }
            ),
        )
        self.assertEqual(
            channel.url_for(channel.api_base, "/pay/debit/task"),
            "https://api.millerpay-app.com/app/pay/debit/task",
        )

    def test_rejects_write_path_before_url_is_returned(self):
        with self.assertRaisesRegex(ValueError, "Unsupported API endpoint"):
            spec().url_for(spec().api_base, "/pay/receive/task")

    def test_rejects_untrusted_configured_base(self):
        for value in (
            "http://api.millerpay-app.com/app",
            "https://evil.example/app",
            "https://api.millerpay-app.com/app/extra",
            "https://user:password@api.millerpay-app.com/app",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "trusted API endpoint"
            ):
                spec().url_for(value, "/auth/login")

    def test_rejects_invalid_origin_and_non_exact_allowlist(self):
        with self.assertRaisesRegex(ValueError, "trusted web origin"):
            replace(spec(), web_origin="https://evil.example")
        with self.assertRaisesRegex(ValueError, "exact read-only allowlist"):
            replace(spec(), allowed_paths=frozenset({"/auth/login"}))

    def test_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            spec().name = "other"
