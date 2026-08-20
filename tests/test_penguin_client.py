from unittest import TestCase

from walletquota.providers.penguinpay.client import PenguinPayClient, make_signature
from walletquota.providers.penguinpay.config import PenguinPaySettings


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, payload, headers, timeout):
        self.calls.append((url, dict(payload), dict(headers), timeout))
        return self.responses.pop(0)


class PenguinPayClientTests(TestCase):
    def test_uses_penguin_urls_origin_and_shared_signature(self):
        settings = PenguinPaySettings.from_values(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"}
        )
        transport = ScriptedTransport(
            [
                {
                    "code": 200,
                    "data": {
                        "userId": 50095029,
                        "loginToken": "login-token",
                        "sessionKey": "session-key",
                    },
                },
                {
                    "code": 200,
                    "data": {
                        "total": 0,
                        "list": [],
                        "inrBuyConfig": "",
                        "inrTodayBuyCount": 0,
                    },
                },
            ]
        )
        client = PenguinPayClient(
            settings,
            transport=transport,
            sleep=lambda delay: None,
            clock_ms=lambda: 100,
        )

        client.login()
        result = client.fetch_pool_page(1, 10, 1, 1)

        self.assertEqual(result["total"], 0)
        login_url, login_payload, login_headers, timeout = transport.calls[0]
        self.assertEqual(login_url, "https://api.penguinpay-app.com/app/auth/login")
        self.assertEqual(login_payload, {"phone": "phone", "password": "secret"})
        self.assertEqual(login_headers["Origin"], "https://app-web.penguinpay-app.com")
        self.assertNotIn("Signature", login_headers)
        self.assertEqual(timeout, 30.0)

        pool_url, pool_payload, pool_headers, unused_timeout = transport.calls[1]
        self.assertEqual(pool_url, "https://api.penguinpay-app.com/app/pay/debit/task")
        self.assertEqual(
            pool_payload,
            {
                "page": 1,
                "size": 10,
                "sortType": 1,
                "orderType": 1,
                "ts": 100,
                "userId": 50095029,
            },
        )
        self.assertEqual(
            pool_headers["Signature"], make_signature("session-key", pool_payload)
        )
