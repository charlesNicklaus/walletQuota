from pathlib import Path
from unittest import TestCase

from walletquota.providers.millerpay.config import ConfigError, MillerPaySettings


class MillerPaySettingsTests(TestCase):
    def test_defaults_match_confirmed_deposit_contract(self):
        value = MillerPaySettings.from_values(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"}
        )

        self.assertEqual(value.api_base, "https://api.millerpay-app.com/app")
        self.assertEqual((value.sort_type, value.order_type, value.page_size), (1, 1, 10))
        self.assertEqual(value.request_delay_ms, 150)
        self.assertEqual(value.timeout_seconds, 30.0)
        self.assertNotIn("secret", repr(value))

    def test_requires_both_credentials(self):
        for values in ({}, {"MILLERPAY_PHONE": "phone"}, {"MILLERPAY_PASSWORD": "secret"}):
            with self.subTest(values=tuple(values)), self.assertRaises(ConfigError):
                MillerPaySettings.from_values(values)

    def test_accepts_supported_non_secret_configuration(self):
        value = MillerPaySettings.from_values(
            {
                "MILLERPAY_PHONE": "phone",
                "MILLERPAY_PASSWORD": "secret",
                "MILLERPAY_SORT_TYPE": "2",
                "MILLERPAY_ORDER_TYPE": "3",
                "MILLERPAY_PAGE_SIZE": "25",
                "MILLERPAY_REQUEST_DELAY_MS": "0",
                "MILLERPAY_TIMEOUT_SECONDS": "12.5",
                "REPORT_DIRECTORY": "miller-reports",
            }
        )

        self.assertEqual((value.sort_type, value.order_type, value.page_size), (2, 3, 25))
        self.assertEqual(value.request_delay_ms, 0)
        self.assertEqual(value.timeout_seconds, 12.5)
        self.assertEqual(value.report_directory, Path("miller-reports"))

    def test_normalizes_the_equivalent_default_https_port(self):
        value = MillerPaySettings.from_values(
            {
                "MILLERPAY_PHONE": "phone",
                "MILLERPAY_PASSWORD": "secret",
                "MILLERPAY_API_BASE": "https://api.millerpay-app.com:443/app/",
            }
        )

        self.assertEqual(value.api_base, "https://api.millerpay-app.com/app")

    def test_only_safe_runtime_overrides_are_applied(self):
        value = MillerPaySettings.from_values(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"},
            overrides={
                "phone": "attacker-phone",
                "password": "attacker-password",
                "api_base": "https://evil.example/app",
                "sort_type": 7,
                "order_type": 8,
                "page_size": 50,
                "report_directory": Path("safe-reports"),
                "request_delay_ms": 0,
                "timeout_seconds": 1,
            },
        )

        self.assertEqual(value.phone, "phone")
        self.assertEqual(value.password, "secret")
        self.assertEqual(value.api_base, "https://api.millerpay-app.com/app")
        self.assertEqual((value.sort_type, value.order_type, value.page_size), (7, 8, 50))
        self.assertEqual(value.report_directory, Path("safe-reports"))
        self.assertEqual(value.request_delay_ms, 150)
        self.assertEqual(value.timeout_seconds, 30.0)

    def test_rejects_invalid_numeric_configuration(self):
        cases = (
            ("MILLERPAY_SORT_TYPE", "many", "must be an integer"),
            ("MILLERPAY_ORDER_TYPE", "many", "must be an integer"),
            ("MILLERPAY_PAGE_SIZE", "0", "between 1 and 1000"),
            ("MILLERPAY_PAGE_SIZE", "1001", "between 1 and 1000"),
            ("MILLERPAY_REQUEST_DELAY_MS", "-1", "cannot be negative"),
            ("MILLERPAY_TIMEOUT_SECONDS", "0", "greater than zero"),
            ("MILLERPAY_TIMEOUT_SECONDS", "nan", "greater than zero"),
            ("MILLERPAY_TIMEOUT_SECONDS", "inf", "greater than zero"),
            ("MILLERPAY_TIMEOUT_SECONDS", "-inf", "greater than zero"),
            ("MILLERPAY_TIMEOUT_SECONDS", "soon", "must be a number"),
        )
        for key, raw_value, message in cases:
            with self.subTest(key=key, raw_value=raw_value):
                values = {
                    "MILLERPAY_PHONE": "phone",
                    "MILLERPAY_PASSWORD": "secret",
                    key: raw_value,
                }
                with self.assertRaisesRegex(ConfigError, message):
                    MillerPaySettings.from_values(values)

    def test_rejects_untrusted_api_base_without_echoing_it(self):
        rejected = "https://evil.example/app?secret=raw-value"
        values = {
            "MILLERPAY_PHONE": "phone",
            "MILLERPAY_PASSWORD": "secret",
            "MILLERPAY_API_BASE": rejected,
        }

        with self.assertRaisesRegex(ConfigError, "trusted MillerPay endpoint") as raised:
            MillerPaySettings.from_values(values)

        self.assertNotIn(rejected, str(raised.exception))
