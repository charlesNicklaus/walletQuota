from types import SimpleNamespace
from unittest import TestCase

from walletquota.providers.signed_pool.client import (
    APIError,
    AuthenticationError,
    HTTPStatusError,
    SignedPoolClient,
    make_signature,
)
from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


MILLER_SPEC = ChannelSpec(
    "millerpay",
    "MillerPay",
    "MillerPay",
    "https://api.millerpay-app.com/app",
    "https://app-web.millerpay-app.com",
    "WalletQuota/millerpay/1.0",
    1,
    1,
    READ_ONLY_PATHS,
)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, payload, headers, timeout):
        self.calls.append((url, dict(payload), dict(headers), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def settings():
    return SimpleNamespace(
        phone="test-phone",
        password="test-password",
        api_base=MILLER_SPEC.api_base,
        timeout_seconds=12.0,
    )


def login(session_key="session-one"):
    return {
        "code": 200,
        "message": "success",
        "data": {
            "userId": 17,
            "loginToken": "token",
            "sessionKey": session_key,
        },
    }


def success(**data):
    return {"code": 200, "message": "success", "data": data}


class SignedPoolClientTests(TestCase):
    def test_uses_channel_origin_url_and_unsigned_login(self):
        transport = ScriptedTransport([login()])
        client = SignedPoolClient(settings(), MILLER_SPEC, transport=transport)
        client.login()
        url, payload, headers, timeout = transport.calls[0]
        self.assertEqual(url, MILLER_SPEC.api_base + "/auth/login")
        self.assertEqual(payload, {"phone": "test-phone", "password": "test-password"})
        self.assertEqual(headers["Origin"], MILLER_SPEC.web_origin)
        self.assertEqual(headers["Referer"], MILLER_SPEC.web_origin + "/")
        self.assertNotIn("Signature", headers)
        self.assertEqual(timeout, 12.0)

    def test_transport_retry_rebuilds_strictly_increasing_timestamp_and_signature(self):
        transport = ScriptedTransport(
            [login(), HTTPStatusError(503), HTTPStatusError(429, 0), success(total=0, list=[])]
        )
        sleeps = []
        client = SignedPoolClient(
            settings(),
            MILLER_SPEC,
            transport=transport,
            sleep=sleeps.append,
            clock_ms=lambda: 100,
        )
        client.fetch_pool_page(1, 10, 1, 1)
        pool_calls = transport.calls[1:]
        self.assertEqual([item[1]["ts"] for item in pool_calls], [100, 101, 102])
        self.assertEqual(len({item[2]["Signature"] for item in pool_calls}), 3)
        for unused_url, payload, headers, unused_timeout in pool_calls:
            self.assertEqual(headers["Signature"], make_signature("session-one", payload))
        self.assertEqual(sleeps, [0.5, 0])

    def test_auth_code_relogs_once_and_resigns_with_new_session(self):
        transport = ScriptedTransport(
            [
                login("one"),
                {"code": 1002, "message": "expired", "data": {}},
                login("two"),
                success(total=0, list=[]),
            ]
        )
        client = SignedPoolClient(
            settings(), MILLER_SPEC, transport=transport, clock_ms=lambda: 100
        )
        client.fetch_pool_page(1, 10, 1, 1)
        retry_payload = transport.calls[3][1]
        retry_headers = transport.calls[3][2]
        self.assertEqual(retry_payload["ts"], 101)
        self.assertEqual(retry_headers["Signature"], make_signature("two", retry_payload))

    def test_persistent_auth_failure_is_bounded_and_safe(self):
        transport = ScriptedTransport(
            [
                login("one"),
                {"code": 1001, "message": "raw secret", "data": {}},
                login("two"),
                {"code": 1001, "message": "raw secret", "data": {}},
            ]
        )
        client = SignedPoolClient(settings(), MILLER_SPEC, transport=transport)
        with self.assertRaisesRegex(AuthenticationError, "MillerPay authentication failed"):
            client.fetch_pool_page(1, 10, 1, 1)
        self.assertEqual(len(transport.calls), 4)

    def test_write_path_and_wrong_base_fail_before_transport(self):
        transport = ScriptedTransport([])
        client = SignedPoolClient(settings(), MILLER_SPEC, transport=transport)
        with self.assertRaisesRegex(APIError, "Unsupported API endpoint"):
            client._url("/pay/receive/task")
        client.settings.api_base = "https://evil.example/app"
        with self.assertRaisesRegex(APIError, "trusted API endpoint"):
            client._url("/auth/login")
        self.assertEqual(transport.calls, [])
