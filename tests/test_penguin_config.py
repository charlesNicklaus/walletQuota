from pathlib import Path
from unittest import TestCase

from walletquota.providers.penguinpay.config import ConfigError, PenguinPaySettings


class PenguinPaySettingsTests(TestCase):
    def test_defaults_match_the_confirmed_deposit_contract(self):
        value = PenguinPaySettings.from_values(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"}
        )

        self.assertEqual(value.api_base, "https://api.penguinpay-app.com/app")
        self.assertEqual((value.sort_type, value.order_type, value.page_size), (1, 1, 10))
        self.assertEqual(value.request_delay_ms, 150)
        self.assertEqual(value.timeout_seconds, 30.0)
        self.assertEqual(value.report_directory, Path("reports"))
        self.assertNotIn("secret", repr(value))

    def test_requires_both_credentials(self):
        cases = ({}, {"PENGUINPAY_PHONE": "phone"}, {"PENGUINPAY_PASSWORD": "secret"})
        for values in cases:
            with self.subTest(keys=tuple(values)), self.assertRaises(ConfigError):
                PenguinPaySettings.from_values(values)

    def test_accepts_only_supported_non_secret_values_and_overrides(self):
        value = PenguinPaySettings.from_values(
            {
                "PENGUINPAY_PHONE": "phone",
                "PENGUINPAY_PASSWORD": "secret",
                "PENGUINPAY_API_BASE": "https://api.penguinpay-app.com:443/app/",
                "PENGUINPAY_SORT_TYPE": "2",
                "PENGUINPAY_ORDER_TYPE": "3",
                "PENGUINPAY_PAGE_SIZE": "25",
                "PENGUINPAY_REQUEST_DELAY_MS": "0",
                "PENGUINPAY_TIMEOUT_SECONDS": "12.5",
                "REPORT_DIRECTORY": "penguin-reports",
            },
            overrides={
                "phone": "attacker-phone",
                "password": "attacker-password",
                "api_base": "https://evil.example/app",
                "sort_type": 7,
                "order_type": 8,
                "page_size": 50,
                "report_directory": Path("safe-reports"),
                "request_delay_ms": 1,
                "timeout_seconds": 1,
            },
        )

        self.assertEqual(value.phone, "phone")
        self.assertEqual(value.password, "secret")
        self.assertEqual(value.api_base, "https://api.penguinpay-app.com/app")
        self.assertEqual((value.sort_type, value.order_type, value.page_size), (7, 8, 50))
        self.assertEqual(value.report_directory, Path("safe-reports"))
        self.assertEqual(value.request_delay_ms, 0)
        self.assertEqual(value.timeout_seconds, 12.5)

    def test_rejects_invalid_numeric_and_untrusted_configuration(self):
        cases = (
            ("PENGUINPAY_SORT_TYPE", "many", "must be an integer"),
            ("PENGUINPAY_ORDER_TYPE", "many", "must be an integer"),
            ("PENGUINPAY_PAGE_SIZE", "0", "between 1 and 1000"),
            ("PENGUINPAY_PAGE_SIZE", "1001", "between 1 and 1000"),
            ("PENGUINPAY_REQUEST_DELAY_MS", "-1", "cannot be negative"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "0", "greater than zero"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "nan", "greater than zero"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "inf", "greater than zero"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "soon", "must be a number"),
        )
        for key, raw, message in cases:
            values = {
                "PENGUINPAY_PHONE": "phone",
                "PENGUINPAY_PASSWORD": "secret",
                key: raw,
            }
            with self.subTest(key=key, raw=raw), self.assertRaisesRegex(
                ConfigError, message
            ):
                PenguinPaySettings.from_values(values)

        rejected = "https://evil.example/app?credential=raw-value"
        with self.assertRaisesRegex(ConfigError, "trusted PenguinPay endpoint") as raised:
            PenguinPaySettings.from_values(
                {
                    "PENGUINPAY_PHONE": "phone",
                    "PENGUINPAY_PASSWORD": "secret",
                    "PENGUINPAY_API_BASE": rejected,
                }
            )
        self.assertNotIn(rejected, str(raised.exception))
