from dataclasses import replace
from unittest import TestCase

from walletquota.providers.penguinpay.spec import PENGUINPAY_SPEC


class PenguinPaySpecTests(TestCase):
    def test_identity_defaults_and_exact_read_only_paths(self):
        self.assertEqual(PENGUINPAY_SPEC.name, "penguinpay")
        self.assertEqual(PENGUINPAY_SPEC.display_name, "PenguinPay")
        self.assertEqual(PENGUINPAY_SPEC.api_label, "PenguinPay")
        self.assertEqual(
            PENGUINPAY_SPEC.api_base, "https://api.penguinpay-app.com/app"
        )
        self.assertEqual(
            PENGUINPAY_SPEC.web_origin,
            "https://app-web.penguinpay-app.com",
        )
        self.assertEqual(
            (PENGUINPAY_SPEC.default_sort_type, PENGUINPAY_SPEC.default_order_type),
            (1, 1),
        )
        self.assertEqual(
            PENGUINPAY_SPEC.allowed_paths,
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

    def test_resolves_only_the_trusted_host_and_default_https_port(self):
        self.assertEqual(
            PENGUINPAY_SPEC.url_for(
                "https://api.penguinpay-app.com:443/app/", "/pay/debit/task"
            ),
            "https://api.penguinpay-app.com/app/pay/debit/task",
        )
        for value in (
            "http://api.penguinpay-app.com/app",
            "https://evil.example/app",
            "https://api.penguinpay-app.com:8443/app",
            "https://user:secret@api.penguinpay-app.com/app",
            "https://api.penguinpay-app.com/app/extra",
            "https://api.penguinpay-app.com/app?next=/app",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "trusted API endpoint"
            ):
                PENGUINPAY_SPEC.url_for(value, "/auth/login")

    def test_rejects_write_paths_and_spoofed_origin(self):
        for path in (
            "/pay/receive/task",
            "/pay/submit/utr",
            "/pay/cancel/task",
            "/user/account/wallet",
            "/pay/debit/upis",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "Unsupported API endpoint"
            ):
                PENGUINPAY_SPEC.url_for(PENGUINPAY_SPEC.api_base, path)
        with self.assertRaisesRegex(ValueError, "trusted web origin"):
            replace(PENGUINPAY_SPEC, web_origin="https://evil.example")
