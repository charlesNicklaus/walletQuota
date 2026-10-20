import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge_wallet_reports.py"
SECRET = "FIXTURE_CREDENTIAL_DO_NOT_ECHO"
START = "2025-01-02T01:00:00+00:00"
FINISH = "2025-01-02T01:01:00+00:00"


def order(provider, amount="1.20"):
    return {
        "provider": provider,
        "order_id": "fake-order",
        "amount": amount,
        "currency": "INR",
        "income": "",
        "balance": None,
        "attributes": {"enabled": True},
    }


def standard_report(provider="fakepay", status="success"):
    channel = {
        "status": status,
        "complete": status == "success",
        "orders": [order(provider)],
        "accounts": [],
        "metadata": {"pages_fetched": 1, "stable": True},
        "summary": {"order_count": 999, "order_amount": "999"},
    }
    if status != "success":
        channel["error"] = {"category": "incomplete", "message": SECRET}
    return {
        "metadata": {"started_at": START, "finished_at": FINISH, "custom": "kept"},
        "total": {"order_count": 999},
        "channels": {provider: channel},
    }


class MergeWalletReportsTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def write_input(self, name, payload):
        path = self.directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def run_merge(self, *inputs):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(path) for path in inputs),
             "--output-dir", str(self.directory / "reports")],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )

    def merged_payload(self):
        paths = list((self.directory / "reports").glob("*/aggregate.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_merges_standard_and_hoyo_decimal_types_and_original_times(self):
        standard = standard_report()
        standard["metadata"]["fraction"] = 0.25
        standard["channels"]["fakepay"]["metadata"]["ratio"] = 0.125
        standard["channels"]["fakepay"]["orders"][0]["attributes"]["ratio"] = 0.5
        hoyo = {
            "provider": "hoyopay", "status": "success", "complete": True,
            "orders": [order("hoyopay", 0.3)], "accounts": [],
            "metadata": {"started_at": "2025-01-01T23:00:00+00:00",
                         "finished_at": "2025-01-02T04:00:00+00:00", "stable": True},
            "pools": [{"order_type": 1, "complete": True, "observed_totals": [1]}],
            "summary": {"order_count": 999, "order_amount": "999"},
        }
        first = self.write_input("standard.json", standard)
        second = self.write_input("hoyo.json", hoyo)
        original_bytes = [path.read_bytes() for path in (first, second)]

        process = self.run_merge(first, second)

        self.assertEqual(process.returncode, 0, process.stderr)
        result = self.merged_payload()
        self.assertEqual(result["total"]["order_count"], 2)
        self.assertEqual(result["total"]["order_amount"], "1.5")
        self.assertEqual(result["metadata"]["started_at"], hoyo["metadata"]["started_at"])
        self.assertEqual(result["metadata"]["finished_at"], hoyo["metadata"]["finished_at"])
        self.assertEqual(result["channels"]["fakepay"]["metadata"]["pages_fetched"], 1)
        self.assertEqual(result["channels"]["fakepay"]["metadata"]["ratio"], 0.125)
        self.assertEqual(result["channels"]["fakepay"]["orders"][0]["attributes"]["ratio"], 0.5)
        self.assertEqual(result["channels"]["fakepay"]["metadata"]["source_reports"][0]["metadata"], standard["metadata"])
        self.assertEqual(result["channels"]["hoyopay"]["metadata"]["pool_snapshots"], hoyo["pools"])
        self.assertIn("订单池综合报告", process.stdout)
        self.assertIn("非同时快照", process.stdout)
        self.assertEqual(original_bytes, [path.read_bytes() for path in (first, second)])

    def test_duplicate_providers_are_rejected_without_writing_reports(self):
        first = self.write_input("one.json", standard_report())
        second = self.write_input("two.json", standard_report())

        process = self.run_merge(first, second)

        self.assertNotEqual(process.returncode, 0)
        self.assertIn("ValueError", process.stderr)
        self.assertFalse((self.directory / "reports").exists())

    def test_partial_and_error_snapshots_do_not_enter_trusted_total(self):
        payload = standard_report()
        payload["channels"].update(standard_report("partialpay", "partial")["channels"])
        error = standard_report("failedpay", "error")["channels"]["failedpay"]
        error["orders"] = []
        payload["channels"]["failedpay"] = error
        source = self.write_input("mixed.json", payload)

        process = self.run_merge(source)

        self.assertEqual(process.returncode, 0, process.stderr)
        result = self.merged_payload()
        self.assertEqual(result["metadata"]["status"], "partial")
        self.assertEqual(result["total"]["order_count"], 1)
        self.assertEqual(result["total"]["channel_partial"], 1)
        self.assertEqual(result["total"]["channel_error"], 1)
        self.assertEqual(result["channels"]["partialpay"]["status"], "partial")
        self.assertNotIn(SECRET, process.stdout + process.stderr)

    def test_hoyo_partial_without_error_is_preserved_and_excluded_from_total(self):
        hoyo = {
            "provider": "hoyopay", "status": "partial", "complete": False,
            "orders": [order("hoyopay", "7.25")], "accounts": [],
            "metadata": {"started_at": START, "finished_at": FINISH},
            "pools": [{"order_type": 1, "complete": False, "observed_totals": [2]}],
        }
        standard = self.write_input("standard.json", standard_report())
        partial = self.write_input("hoyo-partial.json", hoyo)

        process = self.run_merge(standard, partial)

        self.assertEqual(process.returncode, 0, process.stderr)
        result = self.merged_payload()
        self.assertEqual(result["total"]["order_count"], 1)
        self.assertEqual(result["total"]["order_amount"], "1.2")
        self.assertEqual(result["total"]["channel_partial"], 1)
        channel = result["channels"]["hoyopay"]
        self.assertEqual(channel["status"], "partial")
        self.assertFalse(channel["complete"])
        self.assertEqual(channel["summary"]["order_amount"], "7.25")
        self.assertEqual(channel["metadata"]["pool_snapshots"], hoyo["pools"])
        self.assertEqual(channel["error"], {
            "category": "incomplete", "message": "Saved snapshot is incomplete",
        })

    def test_invalid_supplied_error_is_rejected_without_echoing_payload(self):
        for error in (SECRET, None, {"category": "incomplete", "message": [SECRET]}):
            with self.subTest(error_type=type(error).__name__):
                payload = standard_report(status="partial")
                payload["channels"]["fakepay"]["error"] = error
                source = self.write_input("invalid-error.json", payload)

                process = self.run_merge(source)

                self.assertNotEqual(process.returncode, 0)
                self.assertNotIn(SECRET, process.stdout + process.stderr)
                self.assertFalse((self.directory / "reports").exists())

    def test_existing_reporting_cleans_sensitive_nested_attributes(self):
        payload = standard_report()
        channel = payload["channels"]["fakepay"]
        channel["orders"][0]["attributes"] = {"safe": "kept", "token": SECRET}
        channel["metadata"]["nested"] = {"password": SECRET, "enabled": True}
        channel["accounts"] = [{
            "provider": "fakepay", "kind": "bound_upi", "account_id": "fake-account",
            "upi": "fictional@example", "attributes": {"cookie": SECRET, "enabled": True},
        }]
        source = self.write_input("credentials.json", payload)

        process = self.run_merge(source)

        self.assertEqual(process.returncode, 0, process.stderr)
        result = self.merged_payload()["channels"]["fakepay"]
        self.assertEqual(result["orders"][0]["attributes"], {"safe": "kept"})
        self.assertEqual(result["metadata"]["nested"], {"enabled": True})
        self.assertEqual(result["accounts"][0]["attributes"], {"enabled": True})
        for path in (self.directory / "reports").rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET, path.read_text(encoding="utf-8"))
        self.assertNotIn("fictional@example", process.stdout)

    def test_unknown_status_and_invalid_amount_fail_without_echoing_payload(self):
        for change in ("unknown", "invalid_amount"):
            with self.subTest(change=change):
                payload = standard_report()
                channel = payload["channels"]["fakepay"]
                if change == "unknown":
                    channel["status"] = SECRET
                else:
                    channel["orders"][0]["amount"] = SECRET
                source = self.write_input("invalid.json", payload)

                process = self.run_merge(source)

                self.assertNotEqual(process.returncode, 0)
                self.assertNotIn(SECRET, process.stdout + process.stderr)
                self.assertNotIn("Traceback", process.stderr)
                self.assertFalse((self.directory / "reports").exists())

    def test_rejects_path_traversal_provider_and_invalid_collection_times(self):
        for change in ("provider", "naive_time", "reversed_time"):
            with self.subTest(change=change):
                payload = standard_report()
                if change == "provider":
                    channel = payload["channels"].pop("fakepay")
                    channel["orders"][0]["provider"] = "../../escape"
                    payload["channels"]["../../escape"] = channel
                elif change == "naive_time":
                    payload["metadata"]["started_at"] = "2025-01-02T01:00:00"
                else:
                    payload["metadata"]["finished_at"] = "2025-01-01T00:00:00+00:00"
                source = self.write_input("unsafe.json", payload)

                process = self.run_merge(source)

                self.assertNotEqual(process.returncode, 0)
                self.assertIn("ValueError", process.stderr)
                self.assertNotIn("../../escape", process.stderr)
                self.assertFalse((self.directory / "reports").exists())
