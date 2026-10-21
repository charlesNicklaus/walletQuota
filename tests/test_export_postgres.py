import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/export_postgres.py"
SECRET = "FIXTURE_SECRET_MUST_NOT_BE_EXPORTED"


def report():
    return {
        "metadata": {
            "started_at": "2026-01-02T09:00:00+08:00",
            "finished_at": "2026-01-02T09:01:00+08:00",
            "schema_version": 1,
        },
        "channels": {
            "fakepay": {
                "status": "success", "complete": True,
                "summary": {"order_count": 999, "order_amount": "999"},
                "metadata": {"pages_fetched": 1, "token": SECRET},
                "orders": [{
                    "provider": "fakepay", "order_id": "fictional-order",
                    "amount": "123.4567890123", "currency": "INR",
                    "income": "", "balance": "0.01",
                    "attributes": {"safe": "kept", "password": SECRET},
                }],
                "accounts": [{
                    "provider": "fakepay", "kind": "bound_upi",
                    "account_id": "fictional-account", "upi": "fixture@fictional",
                    "attributes": {"cookie": SECRET},
                }],
            },
        },
    }


class ExportPostgresTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def source(self, payload):
        path = self.directory / "aggregate.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def run_export(self, source, output):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(source), "--output", str(output)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_exports_all_four_tables_with_exact_amounts_and_private_file(self):
        source = self.source(report())
        original = source.read_bytes()
        output = self.directory / "nested/data.sql"

        process = self.run_export(source, output)

        self.assertEqual(process.returncode, 0, process.stderr)
        sql = output.read_text(encoding="utf-8")
        for table in ("wallet_crawl_runs", "wallet_channel_snapshots", "wallet_orders", "wallet_accounts"):
            self.assertIn("INSERT INTO public." + table, sql)
        self.assertIn("123.4567890123", sql)
        self.assertIn("NULL", sql)
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertIn("standard_conforming_strings", sql)
        self.assertTrue(sql.strip().endswith("COMMIT;"))
        self.assertNotIn(SECRET, sql + process.stdout + process.stderr)
        self.assertNotIn("fixture@fictional", process.stdout + process.stderr)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(source.read_bytes(), original)

    def test_same_report_has_same_identity_and_changed_report_keeps_history(self):
        payload = report()
        source = self.source(payload)
        first, second, third = (self.directory / name for name in ("one.sql", "two.sql", "three.sql"))
        for output in (first, second):
            process = self.run_export(source, output)
            self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        payload["metadata"]["finished_at"] = "2026-01-02T09:02:00+08:00"
        source = self.source(payload)
        process = self.run_export(source, third)
        self.assertEqual(process.returncode, 0, process.stderr)
        uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        self.assertNotEqual(re.search(uuid_pattern, first.read_text()).group(), re.search(uuid_pattern, third.read_text()).group())

    def test_sql_strings_preserve_quotes_backslashes_newlines_and_unicode(self):
        payload = report()
        payload["channels"]["fakepay"]["orders"][0]["order_id"] = "fictional'\\\n雪; SELECT 1;"
        source = self.source(payload)
        output = self.directory / "text.sql"

        process = self.run_export(source, output)

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("E'fictional''\\\\\n雪; SELECT 1;", output.read_text())
        self.assertNotIn("fictional'", process.stdout + process.stderr)

    def test_partial_and_error_snapshots_keep_status_without_raw_error_text(self):
        payload = report()
        channel = payload["channels"]["fakepay"]
        channel.update(status="partial", complete=False, error={"category": "incomplete", "message": SECRET})
        payload["channels"]["failedpay"] = {
            "status": "error", "complete": False, "orders": [], "accounts": [],
            "metadata": {}, "error": {"category": "api", "message": SECRET},
        }
        source = self.source(payload)
        output = self.directory / "partial.sql"

        process = self.run_export(source, output)

        self.assertEqual(process.returncode, 0, process.stderr)
        sql = output.read_text()
        self.assertIn("'partial'", sql)
        self.assertIn("'error'", sql)
        self.assertIn("123.4567890123", sql)
        self.assertNotIn(SECRET, sql + process.stdout + process.stderr)

    def test_invalid_report_does_not_replace_an_existing_export_or_echo_values(self):
        for invalid in ("invalid_amount", "nul_attribute", "naive_time"):
            with self.subTest(invalid=invalid):
                payload = report()
                if invalid == "invalid_amount":
                    payload["channels"]["fakepay"]["orders"][0]["amount"] = SECRET
                elif invalid == "nul_attribute":
                    payload["channels"]["fakepay"]["orders"][0]["attributes"]["safe"] = "bad\x00" + SECRET
                else:
                    payload["metadata"]["started_at"] = "2026-01-02T09:00:00"
                source = self.source(payload)
                output = self.directory / "existing.sql"
                output.write_text("previous complete export")

                process = self.run_export(source, output)

                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(output.read_text(), "previous complete export")
                self.assertNotIn(SECRET, process.stdout + process.stderr)
                self.assertNotIn("Traceback", process.stderr)

    def test_output_cannot_replace_the_source_report(self):
        source = self.source(report())
        original = source.read_bytes()

        process = self.run_export(source, source)

        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(source.read_bytes(), original)
