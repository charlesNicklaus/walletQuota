import io
import json
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from scripts import hoyopay_readonly
from walletquota.providers.signed_pool.client import APIError


def order(order_id, amount):
    return {"orderNo": order_id, "orderAmount": amount, "income": "2", "balance": "102"}


class FakeClient:
    def __init__(self, pools):
        self.pools = pools
        self.calls = []

    def login(self):
        self.calls.append("login")

    def fetch_pool_page(self, page, size, sort_type, order_type):
        self.calls.append((order_type, page))
        return self.pools[order_type][page]

    def fetch_bound_upis(self, account_type):
        self.calls.append("bound_upis")
        return {"bindUpiList": [{"id": "upi", "upi": "bound@upi"}]}

    def fetch_bank_cards(self):
        self.calls.append("bank_cards")
        return {"list": [{"id": "bank", "cardNumber": "12345678"}]}

    def fetch_my_tasks_page(self, page, size):
        self.calls.append("my_tasks")
        return {"total": 1, "list": [{"orderNo": "claimed"}]}

    def fetch_order_detail(self, order_id):
        self.calls.append("order_detail")
        return {"toUpi": "payee@upi"}


class HoyoPayReadonlyTests(TestCase):
    def test_collects_all_pages_of_both_pools_and_deduplicates_shared_orders(self):
        module = hoyopay_readonly
        first = order("A", "100")
        first["vip"] = {"level": 2, "sessionKey": "must-not-leak", "nested": {"token": "must-not-leak", "usable": True}}
        client = FakeClient({
            1: {1: {"total": 2, "list": [first]}, 2: {"total": 2, "list": [order("B", "200")]}},
            2: {1: {"total": 2, "list": [order("B", "200")]}, 2: {"total": 2, "list": [order("C", "300")]}},
        })
        settings = module.Settings("local-phone", "local-password", page_size=1, request_delay_ms=0)

        result = module.collect(settings, client)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["complete"])
        self.assertEqual(result["summary"]["order_count"], 3)
        self.assertEqual(result["summary"]["order_amount"], "600")
        self.assertEqual(result["metadata"]["cross_pool_duplicate_count"], 1)
        self.assertEqual([item["pages_fetched"] for item in result["pools"]], [2, 2])
        self.assertEqual(client.calls[:5], ["login", (1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertEqual({item["kind"] for item in result["accounts"]}, {"bound_upi", "bank_account", "order_payee_upi"})
        self.assertEqual(result["orders"][0]["attributes"]["vip"], {"level": 2, "nested": {"usable": True}})
        self.assertNotIn("must-not-leak", json.dumps(result))
        self.assertEqual(settings.order_type, 1)

    def test_conflicting_shared_order_is_partial_and_cli_returns_three(self):
        module = hoyopay_readonly
        client = FakeClient({
            1: {1: {"total": 1, "list": [order("A", "100")]}},
            2: {1: {"total": 1, "list": [order("A", "150")]}},
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "settings.env"
            env.write_text("HOYOPAY_PHONE=private-phone\nHOYOPAY_PASSWORD=private-password\n")
            output = root / "nested/result.json"
            stdout = io.StringIO()
            with patch.object(module, "SignedPoolClient", return_value=client):
                code = module.main(["--env-file", str(env), "--output", str(output)], stdout=stdout)
            result = json.loads(output.read_text())
            self.assertEqual(code, 3)
            self.assertEqual(result["status"], "partial")
            self.assertFalse(result["complete"])
            self.assertEqual(result["metadata"]["conflicting_order_count"], 1)
            self.assertEqual(result["summary"]["order_amount"], "100")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private-", stdout.getvalue())
            self.assertNotIn("bound@upi", stdout.getvalue())
            self.assertNotIn("12345678", stdout.getvalue())

    def test_same_pool_conflicting_duplicates_are_partial_but_identical_repeats_are_complete(self):
        module = hoyopay_readonly
        for repeated_amount, expected_status in (("150", "partial"), ("100", "success")):
            with self.subTest(repeated_amount=repeated_amount):
                client = FakeClient({
                    1: {
                        1: {"total": 3, "list": [order("A", "100"), order("B", "200")]},
                        2: {"total": 3, "list": [order("A", repeated_amount), order("C", "300")]},
                    },
                    2: {1: {"total": 0, "list": []}},
                })
                settings = module.Settings("local", "secret", page_size=2, request_delay_ms=0)

                result = module.collect(settings, client)

                conflicts = int(expected_status == "partial")
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["complete"], not conflicts)
                self.assertEqual(result["summary"]["order_count"], 3)
                self.assertEqual(result["summary"]["order_amount"], "600")
                self.assertEqual(result["metadata"]["duplicate_count"], 1)
                self.assertEqual(result["metadata"]["same_pool_conflicting_order_count"], conflicts)
                self.assertEqual(result["metadata"]["conflicting_order_count"], conflicts)
                self.assertEqual(result["pools"][0]["conflicting_order_count"], conflicts)
                self.assertEqual(result["pools"][0]["complete"], not conflicts)

    def test_first_page_cap_restarts_at_actual_size_with_fresh_conflict_tracking(self):
        module = hoyopay_readonly

        class CappedClient(FakeClient):
            def __init__(self):
                super().__init__({})

            def fetch_pool_page(self, page, size, sort_type, order_type):
                self.calls.append((order_type, page, size))
                if order_type == 2:
                    return {"total": 0, "list": []}
                if size == 100:
                    return {"total": 2, "list": [order("A", "100"), order("A", "150")]}
                return {"total": 2, "list": [order("A" if page == 1 else "B", "100" if page == 1 else "200")]}

        client = CappedClient()
        settings = module.Settings("local", "secret", request_delay_ms=0)

        result = module.collect(settings, client)

        self.assertEqual(client.calls[:5], ["login", (1, 1, 100), (1, 1, 1), (1, 2, 1), (2, 1, 100)])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary"]["order_count"], 2)
        self.assertEqual(result["summary"]["order_amount"], "300")
        self.assertEqual(result["metadata"]["same_pool_conflicting_order_count"], 0)
        self.assertEqual(result["pools"][0]["conflicting_order_count"], 0)
        self.assertEqual(result["pools"][0]["pages_fetched"], 2)
        self.assertEqual(settings.page_size, 100)

    def test_missing_credentials_and_server_errors_do_not_leak_values(self):
        module = hoyopay_readonly
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "settings.env"
            env.write_text("HOYOPAY_PHONE=do-not-print-phone\n")
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(module, "SignedPoolClient") as factory:
                code = module.main(["--env-file", str(env)], stdout=stdout, stderr=stderr)
            self.assertEqual(code, 1)
            factory.assert_not_called()
            self.assertNotIn("do-not-print-phone", stdout.getvalue() + stderr.getvalue())

            env.write_text("HOYOPAY_PHONE=local\nHOYOPAY_PASSWORD=do-not-print-password\n")
            with patch.object(module, "SignedPoolClient", side_effect=APIError("raw-auth-secret")):
                code = module.main(["--env-file", str(env)], stdout=stdout, stderr=stderr)
            self.assertNotEqual(code, 0)
            self.assertNotIn("raw-auth-secret", stdout.getvalue() + stderr.getvalue())
            self.assertNotIn("do-not-print-password", stdout.getvalue() + stderr.getvalue())

    def test_endpoint_rejects_mutations_and_untrusted_hosts(self):
        module = hoyopay_readonly
        self.assertEqual(module.endpoint(module.API_BASE, "/pay/debit/task"), module.API_BASE + "/pay/debit/task")
        for path in ("/auth/register", "/auth/send/code", "/pay/receive/task", "/pay/submit/utr", "/bank/debit/add/card"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                module.endpoint(module.API_BASE, path)
        for base in ("https://untrusted.example/app", "http://api.hoyopay-app.com/app", module.API_BASE + "/"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                module.endpoint(base, "/auth/login")

    def test_failed_atomic_write_preserves_existing_private_result(self):
        module = hoyopay_readonly
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            output.write_text("old complete result\n")
            os.chmod(output, 0o600)
            with self.assertRaises(TypeError):
                module.write_result(output, {"invalid": object()})
            self.assertEqual(output.read_text(), "old complete result\n")
            self.assertEqual(list(Path(directory).iterdir()), [output])
