import unittest
from pathlib import Path

from atg_pool.client import (
    APIError,
    AtgClient,
    AuthenticationError,
    HTTPStatusError,
    TransportError,
    make_signature,
)
from atg_pool.config import Settings


class SignatureTests(unittest.TestCase):
    def test_sorts_payload_keys_and_appends_session_key(self):
        payload = {
            "page": 2,
            "size": 10,
            "sortType": 1,
            "orderType": 4,
            "ts": 1786520228508,
            "userId": 50095029,
        }

        signature = make_signature("secret-key", payload)

        self.assertEqual(signature, "1b9d1e3b06a3fa98ee8b4ae3056e09f7")

    def test_uses_javascript_style_boolean_and_null_values(self):
        payload = {"enabled": True, "optional": None}

        signature = make_signature("key", payload)

        import hashlib

        expected = hashlib.md5(b"enabled=true&optional=null&key").hexdigest()
        self.assertEqual(signature, expected)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, payload, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "payload": dict(payload),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def settings():
    return Settings(
        phone="9876543210",
        password="local-secret",
        api_base="https://api.atg-game.com/app",
        request_delay_ms=0,
        timeout_seconds=12,
        report_directory=Path("reports"),
    )


def login_response(session_key="session-one"):
    return {
        "code": 200,
        "message": "success",
        "data": {
            "userId": 50095029,
            "loginToken": "login-token",
            "sessionKey": session_key,
        },
    }


def pool_response(total=1):
    return {
        "code": 200,
        "message": "success",
        "data": {
            "total": total,
            "list": [],
            "inrBuyConfig": "",
            "inrTodayBuyCount": 0,
        },
    }


def read_response(**data):
    return {"code": 200, "message": "success", "data": data}


class AtgClientTests(unittest.TestCase):
    def test_login_is_unsigned_and_extracts_session(self):
        transport = ScriptedTransport([login_response()])
        client = AtgClient(settings(), transport=transport, sleep=lambda _: None)

        client.login()

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.atg-game.com/app/auth/login")
        self.assertEqual(
            call["payload"], {"phone": "9876543210", "password": "local-secret"}
        )
        self.assertNotIn("Signature", call["headers"])
        self.assertEqual(client.user_id, 50095029)
        self.assertEqual(client.session_key, "session-one")
        self.assertEqual(client.login_token, "login-token")

    def test_login_rejects_incomplete_session_without_leaking_password(self):
        transport = ScriptedTransport(
            [{"code": 200, "message": "success", "data": {"userId": 12}}]
        )
        client = AtgClient(settings(), transport=transport, sleep=lambda _: None)

        with self.assertRaises(AuthenticationError) as raised:
            client.login()

        self.assertNotIn("local-secret", str(raised.exception))

    def test_login_rejection_does_not_expose_server_authentication_message(self):
        transport = ScriptedTransport(
            [
                {
                    "code": 401,
                    "message": "invalid login for 9876543210 using local-secret",
                    "data": {},
                }
            ]
        )
        client = AtgClient(settings(), transport=transport, sleep=lambda _: None)

        with self.assertRaises(AuthenticationError) as raised:
            client.login()

        self.assertEqual(str(raised.exception), "ATG authentication failed")
        self.assertNotIn("9876543210", str(raised.exception))
        self.assertNotIn("local-secret", str(raised.exception))

    def test_fetch_pool_page_signs_final_payload(self):
        transport = ScriptedTransport([login_response(), pool_response(299)])
        timestamps = iter([1786520228508])
        client = AtgClient(
            settings(),
            transport=transport,
            sleep=lambda _: None,
            clock_ms=lambda: next(timestamps),
        )
        client.login()

        data = client.fetch_pool_page(2, 10, 1, 4)

        call = transport.calls[1]
        expected_payload = {
            "page": 2,
            "size": 10,
            "sortType": 1,
            "orderType": 4,
            "ts": 1786520228508,
            "userId": 50095029,
        }
        self.assertEqual(call["payload"], expected_payload)
        self.assertEqual(
            call["headers"]["Signature"],
            make_signature("session-one", expected_payload),
        )
        self.assertEqual(data["total"], 299)

    def test_transient_transport_error_is_retried_twice(self):
        transport = ScriptedTransport(
            [
                login_response(),
                TransportError("temporary network failure"),
                HTTPStatusError(503),
                pool_response(),
            ]
        )
        sleeps = []
        timestamps = iter([100, 101, 102])
        client = AtgClient(
            settings(),
            transport=transport,
            sleep=sleeps.append,
            clock_ms=lambda: next(timestamps),
        )
        client.login()

        client.fetch_pool_page(1, 10, 1, 4)

        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(sleeps, [0.5, 1.0])
        pool_calls = transport.calls[1:]
        self.assertEqual([call["payload"]["ts"] for call in pool_calls], [100, 101, 102])
        self.assertEqual(len({call["headers"]["Signature"] for call in pool_calls}), 3)

    def test_session_code_relogs_once_and_resigns_with_new_key(self):
        transport = ScriptedTransport(
            [
                login_response("session-one"),
                {"code": 1002, "message": "refresh required", "data": {}},
                login_response("session-two"),
                pool_response(2),
            ]
        )
        timestamps = iter([100, 101])
        client = AtgClient(
            settings(),
            transport=transport,
            sleep=lambda _: None,
            clock_ms=lambda: next(timestamps),
        )
        client.login()

        result = client.fetch_pool_page(1, 10, 1, 4)

        self.assertEqual(result["total"], 2)
        self.assertEqual(transport.calls[2]["url"].split("/app")[-1], "/auth/login")
        retry_call = transport.calls[3]
        self.assertEqual(
            retry_call["headers"]["Signature"],
            make_signature("session-two", retry_call["payload"]),
        )

    def test_session_code_is_not_retried_forever(self):
        transport = ScriptedTransport(
            [
                login_response(),
                {"code": 1001, "message": "expired", "data": {}},
                login_response("new-session"),
                {"code": 1001, "message": "expired", "data": {}},
            ]
        )
        client = AtgClient(
            settings(), transport=transport, sleep=lambda _: None, clock_ms=lambda: 1
        )
        client.login()

        with self.assertRaises(AuthenticationError) as raised:
            client.fetch_pool_page(1, 10, 1, 4)

        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(str(raised.exception), "ATG authentication failed")

    def test_non_success_envelope_raises_api_error(self):
        transport = ScriptedTransport(
            [login_response(), {"code": 4004, "message": "bad filters", "data": {}}]
        )
        client = AtgClient(
            settings(), transport=transport, sleep=lambda _: None, clock_ms=lambda: 1
        )
        client.login()

        with self.assertRaisesRegex(APIError, "bad filters"):
            client.fetch_pool_page(1, 10, 1, 4)

    def test_fetch_bound_upis_logs_in_and_signs_account_type_payload(self):
        transport = ScriptedTransport([login_response(), read_response(list=[])])
        client = AtgClient(
            settings(), transport=transport, sleep=lambda _: None, clock_ms=lambda: 101
        )

        result = client.fetch_bound_upis()

        call = transport.calls[1]
        expected = {"accountType": 1, "ts": 101, "userId": 50095029}
        self.assertEqual(
            call["url"], "https://api.atg-game.com/app/user/upi/bind/list"
        )
        self.assertEqual(call["payload"], expected)
        self.assertEqual(call["headers"]["Signature"], make_signature("session-one", expected))
        self.assertEqual(result, {"list": []})

    def test_fetch_bank_cards_logs_in_and_signs_final_payload(self):
        transport = ScriptedTransport([login_response(), read_response(list=[])])
        client = AtgClient(
            settings(), transport=transport, sleep=lambda _: None, clock_ms=lambda: 102
        )

        result = client.fetch_bank_cards()

        call = transport.calls[1]
        expected = {"ts": 102, "userId": 50095029}
        self.assertEqual(call["url"], "https://api.atg-game.com/app/bank/debit/card/list")
        self.assertEqual(call["payload"], expected)
        self.assertEqual(call["headers"]["Signature"], make_signature("session-one", expected))
        self.assertEqual(result, {"list": []})

    def test_fetch_my_tasks_page_signs_pagination_payload(self):
        transport = ScriptedTransport([login_response(), read_response(total=0, list=[])])
        client = AtgClient(
            settings(), transport=transport, sleep=lambda _: None, clock_ms=lambda: 103
        )

        result = client.fetch_my_tasks_page(2, 15)

        call = transport.calls[1]
        expected = {"page": 2, "size": 15, "ts": 103, "userId": 50095029}
        self.assertEqual(call["url"], "https://api.atg-game.com/app/pay/my/task")
        self.assertEqual(call["payload"], expected)
        self.assertEqual(call["headers"]["Signature"], make_signature("session-one", expected))
        self.assertEqual(result, {"total": 0, "list": []})

    def test_fetch_order_detail_signs_order_number_payload(self):
        transport = ScriptedTransport([login_response(), read_response(orderNo="ATG-42")])
        client = AtgClient(
            settings(), transport=transport, sleep=lambda _: None, clock_ms=lambda: 104
        )

        result = client.fetch_order_detail("ATG-42")

        call = transport.calls[1]
        expected = {"orderNo": "ATG-42", "ts": 104, "userId": 50095029}
        self.assertEqual(call["url"], "https://api.atg-game.com/app/pay/order/detail")
        self.assertEqual(call["payload"], expected)
        self.assertEqual(call["headers"]["Signature"], make_signature("session-one", expected))
        self.assertEqual(result, {"orderNo": "ATG-42"})

    def test_signed_read_relogs_once_and_rebuilds_signature_after_session_code(self):
        transport = ScriptedTransport(
            [
                login_response("session-one"),
                {"code": 1002, "message": "refresh required", "data": {}},
                login_response("session-two"),
                read_response(list=[]),
            ]
        )
        timestamps = iter([201, 202])
        client = AtgClient(
            settings(),
            transport=transport,
            sleep=lambda _: None,
            clock_ms=lambda: next(timestamps),
        )

        client.fetch_bound_upis(2)

        first_read, retry_read = transport.calls[1], transport.calls[3]
        self.assertEqual(transport.calls[2]["url"], "https://api.atg-game.com/app/auth/login")
        self.assertEqual(first_read["payload"]["ts"], 201)
        self.assertEqual(retry_read["payload"]["ts"], 202)
        self.assertEqual(
            retry_read["headers"]["Signature"],
            make_signature("session-two", retry_read["payload"]),
        )

    def test_signed_read_does_not_relog_more_than_once(self):
        transport = ScriptedTransport(
            [
                login_response(),
                {"code": 1001, "message": "expired", "data": {}},
                login_response("session-two"),
                {"code": 1001, "message": "expired", "data": {}},
            ]
        )
        client = AtgClient(
            settings(), transport=transport, sleep=lambda _: None, clock_ms=lambda: 1
        )

        with self.assertRaises(AuthenticationError):
            client.fetch_bank_cards()

        self.assertEqual(len(transport.calls), 4)

    def test_signed_read_rejects_empty_order_numbers_and_invalid_pagination_locally(self):
        transport = ScriptedTransport([])
        client = AtgClient(settings(), transport=transport, sleep=lambda _: None)

        with self.assertRaisesRegex(ValueError, "order number"):
            client.fetch_order_detail("")
        with self.assertRaisesRegex(ValueError, "page"):
            client.fetch_my_tasks_page(0, 10)
        with self.assertRaisesRegex(ValueError, "size"):
            client.fetch_my_tasks_page(1, 0)

        self.assertEqual(transport.calls, [])

    def test_url_rejects_non_allowlisted_endpoints(self):
        client = AtgClient(settings(), transport=ScriptedTransport([]), sleep=lambda _: None)

        with self.assertRaisesRegex(APIError, "Unsupported API endpoint"):
            client._url("/arbitrary/read")


if __name__ == "__main__":
    unittest.main()
