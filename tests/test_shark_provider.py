import hashlib
from dataclasses import replace
from decimal import Decimal
from unittest import TestCase, mock

from walletquota.providers.sharkpay.client import SharkPayClient
from walletquota.providers.sharkpay.config import ConfigError, SharkPaySettings
from walletquota.providers.sharkpay.provider import SharkPayProvider
from walletquota.providers.sharkpay.spec import SHARKPAY_SPEC
from walletquota.providers.signed_pool.client import APIError, AuthenticationError
from walletquota.providers.signed_pool.pool import PoolDataError
from walletquota.providers.signed_pool.spec import READ_ONLY_PATHS


VALUES = {"SHARKPAY_PHONE": "fixture-phone", "SHARKPAY_PASSWORD": "fixture-secret"}


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, payload, headers, timeout):
        self.calls.append((url, dict(payload), dict(headers), timeout))
        return {"code": 200, "data": self.responses.pop(0)}


def collect_with_transport(responses, **values):
    settings = SharkPaySettings.from_values({
        **VALUES, "SHARKPAY_PAGE_SIZE": "2", "SHARKPAY_REQUEST_DELAY_MS": "0", **values,
    })
    transport = RecordingTransport([{
        "userId": 7, "loginToken": "fixture-token", "sessionKey": "fixture-session",
    }, *responses, {"bindUpiList": []}, {"list": []}, {"total": 0, "list": []}])
    client = SharkPayClient(settings, transport=transport, clock_ms=lambda: 100)
    with mock.patch("walletquota.providers.sharkpay.provider.SharkPayClient", return_value=client):
        result = SharkPayProvider({
            **VALUES, "SHARKPAY_PAGE_SIZE": "2", "SHARKPAY_REQUEST_DELAY_MS": "0", **values,
        }).run()
    return result, transport


class SharkPayProviderTests(TestCase):
    def test_confirmed_defaults_and_channel_configuration_are_isolated(self):
        settings = SharkPaySettings.from_values(VALUES)
        self.assertEqual(SHARKPAY_SPEC.name, "sharkpay")
        self.assertEqual(SHARKPAY_SPEC.api_base, "https://api.sharkpay-app.com/app")
        self.assertEqual(SHARKPAY_SPEC.web_origin, "https://app-web.sharkpay-app.com")
        self.assertEqual(SHARKPAY_SPEC.allowed_paths, READ_ONLY_PATHS)
        self.assertEqual((settings.sort_type, settings.order_type, settings.page_size), (1, 1, 10))
        self.assertNotIn(VALUES["SHARKPAY_PASSWORD"], repr(settings))
        for values in ({}, {"SHARKPAY_PHONE": "fixture-phone"}, {
            "PENGUINPAY_PHONE": "fixture-phone", "PENGUINPAY_PASSWORD": "fixture-secret",
        }):
            with self.subTest(keys=tuple(values)), self.assertRaises(ConfigError):
                SharkPaySettings.from_values(values)
        for endpoint in (
            "https://api.penguinpay-app.com/app", "http://api.sharkpay-app.com/app",
            "https://api.sharkpay-app.com:8443/app", "https://api.sharkpay-app.com/app?token=secret",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ConfigError):
                SharkPaySettings.from_values({**VALUES, "SHARKPAY_API_BASE": endpoint})
        for key, value in (("SHARKPAY_PAGE_SIZE", "0"), ("SHARKPAY_PAGE_SIZE", "1001"),
                           ("SHARKPAY_TIMEOUT_SECONDS", "nan"), ("SHARKPAY_TIMEOUT_SECONDS", "inf")):
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                SharkPaySettings.from_values({**VALUES, key: value})
        with self.assertRaises(ValueError):
            replace(SHARKPAY_SPEC, web_origin="https://app-web.penguinpay-app.com")
        with self.assertRaises(ValueError):
            SHARKPAY_SPEC.url_for(settings.api_base, "/pay/receive/task")

    def test_reads_signed_paginated_pool_and_account_sources_with_shark_identity(self):
        result, transport = collect_with_transport([
            {"total": 3, "list": [
                {"orderNo": "SHARK-1", "orderAmount": "10.25"},
                {"orderNo": "SHARK-2", "orderAmount": "5"},
            ]},
            {"total": 3, "list": [{"orderNo": "SHARK-3", "orderAmount": "7.10"}]},
        ])
        self.assertEqual((result.provider, result.status, result.complete), ("sharkpay", "success", True))
        self.assertEqual(sum((order.amount for order in result.orders), Decimal("0")), Decimal("22.35"))
        self.assertTrue(all(order.provider == "sharkpay" for order in result.orders))
        self.assertEqual(result.metadata["pages_fetched"], 2)
        self.assertEqual([call[0] for call in transport.calls], [
            "https://api.sharkpay-app.com/app" + path for path in (
                "/auth/login", "/pay/debit/task", "/pay/debit/task",
                "/user/upi/bind/list", "/bank/debit/card/list", "/pay/my/task",
            )
        ])
        self.assertEqual(transport.calls[0][1], {"phone": "fixture-phone", "password": "fixture-secret"})
        self.assertNotIn("Signature", transport.calls[0][2])
        for page, call in enumerate(transport.calls[1:3], 1):
            self.assertEqual(call[1], {
                "page": page, "size": 2, "sortType": 1, "orderType": 1, "ts": 100, "userId": 7,
            })
            expected = hashlib.md5((
                "orderType=1&page={}&size=2&sortType=1&ts=100&userId=7&fixture-session".format(page)
            ).encode()).hexdigest()
            self.assertEqual(call[2]["Signature"], expected)
        for call in transport.calls:
            self.assertEqual(call[2]["Origin"], "https://app-web.sharkpay-app.com")
        self.assertEqual(transport.responses, [])

    def test_incomplete_pool_preserves_observed_orders_with_partial_status(self):
        result, _ = collect_with_transport([
            {"total": 3, "list": [{"orderNo": "SHARK-PARTIAL", "orderAmount": "12.34"}]},
        ])
        self.assertEqual((result.status, result.complete), ("partial", False))
        self.assertEqual(result.error.category, "incomplete")
        self.assertEqual(result.orders[0].provider, "sharkpay")
        self.assertEqual(result.orders[0].amount, Decimal("12.34"))

    def test_channel_failures_are_safe_and_missing_credentials_do_not_collect(self):
        collect = mock.Mock()
        result = SharkPayProvider({}, collect=collect).run()
        self.assertEqual(result.error.category, "configuration")
        collect.assert_not_called()
        for error, category in (
            (AuthenticationError("raw-private-error"), "authentication"),
            (APIError("raw-private-error"), "api"),
            (PoolDataError("raw-private-error"), "data"),
        ):
            with self.subTest(category=category):
                result = SharkPayProvider(VALUES, collect=mock.Mock(side_effect=error)).run()
                self.assertEqual(result.provider, "sharkpay")
                self.assertEqual(result.error.category, category)
                self.assertNotIn("raw-private-error", result.error.message)
