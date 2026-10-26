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
    def test_shark_has_its_own_trusted_host_pair(self):
        channel = replace(
            spec(), name="sharkpay", api_base="https://api.sharkpay-app.com/app",
            web_origin="https://app-web.sharkpay-app.com",
        )
        self.assertEqual(
            channel.url_for(channel.api_base, "/pay/debit/task"),
            "https://api.sharkpay-app.com/app/pay/debit/task",
        )
        with self.assertRaisesRegex(ValueError, "trusted API endpoint"):
            channel.url_for("https://api.millerpay-app.com/app", "/auth/login")
        with self.assertRaisesRegex(ValueError, "trusted web origin"):
            replace(channel, web_origin="https://app-web.millerpay-app.com")

    def test_smartwallet_cannot_use_the_shark_host_pair(self):
        channel = replace(
            spec(), name="smartwallet", api_base="https://api.smartwallet-app.com/app",
            web_origin="https://app-web.smartwallet-app.com",
        )
        self.assertEqual(
            channel.url_for(channel.api_base, "/pay/debit/task"),
            "https://api.smartwallet-app.com/app/pay/debit/task",
        )
        with self.assertRaisesRegex(ValueError, "trusted API endpoint"):
            channel.url_for("https://api.sharkpay-app.com/app", "/auth/login")
        with self.assertRaisesRegex(ValueError, "trusted web origin"):
            replace(channel, web_origin="https://app-web.sharkpay-app.com")

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
            "https://api.millerpay-app.com:8443/app",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "trusted API endpoint"
            ):
                spec().url_for(value, "/auth/login")

    def test_normalizes_the_equivalent_default_https_port(self):
        self.assertEqual(
            spec().url_for(
                "https://api.millerpay-app.com:443/app", "/auth/login"
            ),
            "https://api.millerpay-app.com/app/auth/login",
        )

    def test_rejects_spoofed_canonical_host_pair_for_millerpay(self):
        with self.assertRaisesRegex(ValueError, "trusted API endpoint"):
            replace(
                spec(),
                api_base="https://api.evil.example/app",
                web_origin="https://app-web.evil.example",
            )

    def test_rejects_invalid_origin_and_non_exact_allowlist(self):
        with self.assertRaisesRegex(ValueError, "trusted web origin"):
            replace(spec(), web_origin="https://evil.example")
        with self.assertRaisesRegex(ValueError, "exact read-only allowlist"):
            replace(spec(), allowed_paths=frozenset({"/auth/login"}))

    def test_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            spec().name = "other"
