import json
import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from walletquota.retention import cleanup_expired_reports


TODAY = date(2026, 8, 12)


def marked_payload(finished_at):
    return {
        "metadata": {
            "generator": "walletQuota",
            "schema_version": 1,
            "finished_at": finished_at,
        }
    }


def legacy_payload(finished_at):
    return {
        "metadata": {
            "status": "success",
            "started_at": "2026-08-11T08:00:00+00:00",
            "finished_at": finished_at,
            "provider_count": 1,
        },
        "total": {},
        "channels": {
            "example": {
                "status": "success",
                "complete": True,
                "summary": {},
                "metadata": {"safe": True},
                "orders": [],
            }
        },
    }


class ReportRetentionTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "reports"
        self.root.mkdir()

    def snapshot(self, name, payload):
        directory = self.root / name
        directory.mkdir(parents=True)
        (directory / "aggregate.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return directory

    def test_deletes_marked_export_finished_yesterday_in_ist(self):
        report = self.snapshot(
            "20260811_230000", marked_payload("2026-08-11T23:00:00+05:30")
        )

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, (report,))
        self.assertFalse(report.exists())

    def test_preserves_marked_exports_finished_today_or_in_the_future_in_ist(self):
        today = self.snapshot(
            "20260812_000000", marked_payload("2026-08-12T00:00:00+05:30")
        )
        future = self.snapshot(
            "20260812_000001_10", marked_payload("2026-08-13T00:00:00+05:30")
        )

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, ())
        self.assertCountEqual(result.preserved, (today, future))
        self.assertTrue(today.is_dir())
        self.assertTrue(future.is_dir())

    def test_deletes_valid_legacy_walletquota_export_finished_yesterday(self):
        report = self.snapshot(
            "20260811_080000_2", legacy_payload("2026-08-11T08:00:00+05:30")
        )

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, (report,))
        self.assertFalse(report.exists())

    def test_preserves_unrecognized_entries_and_never_follows_symlinks(self):
        corrupted = self.snapshot("20260811_090000", {"metadata": {}})
        (corrupted / "aggregate.json").write_text("{", encoding="utf-8")
        unknown = self.snapshot("20260811_100000", {"not": "a report"})
        invalid_name = self.snapshot("not-a-snapshot", marked_payload("2026-08-11T08:00:00+05:30"))
        ordinary_file = self.root / "notes.txt"
        ordinary_file.write_text("keep", encoding="utf-8")
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        external_report = outside / "20260811_110000"
        external_report.mkdir()
        (external_report / "aggregate.json").write_text(
            json.dumps(marked_payload("2026-08-11T08:00:00+05:30")), encoding="utf-8"
        )
        symlink = self.root / "20260811_120000"
        symlink.symlink_to(external_report, target_is_directory=True)

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, ())
        self.assertTrue(corrupted.is_dir())
        self.assertTrue(unknown.is_dir())
        self.assertTrue(invalid_name.is_dir())
        self.assertTrue(ordinary_file.is_file())
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(external_report.is_dir())
        self.assertIn("Unrecognized report directory preserved", result.warnings)

    def test_preserves_invalid_utf8_aggregate_json_with_a_safe_warning(self):
        report = self.root / "20260811_125000"
        report.mkdir()
        (report / "aggregate.json").write_bytes(b"\xff")

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, ())
        self.assertIn(report, result.preserved)
        self.assertEqual(result.warnings, ("Unrecognized report directory preserved",))

    def test_does_not_scan_below_an_unknown_direct_child(self):
        unknown = self.root / "other"
        nested = self.snapshot("other/20260811_130000", marked_payload("2026-08-11T08:00:00+05:30"))

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, ())
        self.assertTrue(unknown.is_dir())
        self.assertTrue(nested.is_dir())

    def test_preserves_legacy_export_with_nested_credential_field(self):
        payload = legacy_payload("2026-08-11T08:00:00+05:30")
        payload["channels"]["example"]["metadata"] = {
            "safe": True,
            "details": {"sessionKey": "not safe to trust"},
        }
        report = self.snapshot("20260811_140000", payload)

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, ())
        self.assertIn(report, result.preserved)
        self.assertIn("Unrecognized report directory preserved", result.warnings)

    def test_preserves_legacy_export_with_nested_raw_auth_response(self):
        payload = legacy_payload("2026-08-11T08:00:00+05:30")
        payload["channels"]["example"]["metadata"] = {
            "safe": True,
            "details": {"rawAuthResponse": {"status": "received"}},
        }
        report = self.snapshot("20260811_150000", payload)

        result = cleanup_expired_reports(self.root, TODAY)

        self.assertEqual(result.deleted, ())
        self.assertIn(report, result.preserved)
        self.assertIn("Unrecognized report directory preserved", result.warnings)

    def test_preserves_legacy_export_with_nested_authentication_keys(self):
        for index, key in enumerate(
            ("apiKey", "authorization", "credential", "authHeader"), start=16
        ):
            with self.subTest(key=key):
                payload = legacy_payload("2026-08-11T08:00:00+05:30")
                payload["channels"]["example"]["metadata"] = {
                    "safe": True,
                    "details": {key: {"status": "received"}},
                }
                report = self.snapshot("20260811_{}0000".format(index), payload)

                result = cleanup_expired_reports(self.root, TODAY)

                self.assertEqual(result.deleted, ())
                self.assertIn(report, result.preserved)
                self.assertIn("Unrecognized report directory preserved", result.warnings)
