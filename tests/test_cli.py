import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import atg_pool_report
from atg_pool.client import APIError
from atg_pool.config import ConfigError, Settings
from atg_pool.pool import PoolSnapshot


def value_snapshot(complete=True):
    stamp = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
    return PoolSnapshot(
        orders=(),
        observed_totals=(0 if complete else 3,),
        pages_fetched=1,
        duplicate_count=0,
        complete=complete,
        stable=True,
        stop_reason="reached_total" if complete else "empty_page",
        sort_type=1,
        order_type=4,
        started_at=stamp,
        finished_at=stamp,
    )


class CliTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(phone="9876543210", password="secret")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_success_prints_summary_and_report_paths(self):
        stdout = io.StringIO()
        snapshot = value_snapshot()
        csv_path = Path(self.temp_dir.name) / "report.csv"
        json_path = Path(self.temp_dir.name) / "report.json"
        with patch("atg_pool_report.Settings.load", return_value=self.settings), patch(
            "atg_pool_report.AtgClient"
        ) as client_type, patch(
            "atg_pool_report.collect_pool", return_value=snapshot
        ), patch(
            "atg_pool_report.build_summary", return_value={}
        ), patch(
            "atg_pool_report.format_terminal_report", return_value="POOL SUMMARY"
        ), patch(
            "atg_pool_report.write_reports", return_value=(csv_path, json_path)
        ):
            client_type.return_value.login.return_value = None
            code = atg_pool_report.main([], stdout=stdout, stderr=io.StringIO())

        output = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("POOL SUMMARY", output)
        self.assertIn(str(csv_path), output)
        self.assertIn(str(json_path), output)

    def test_configuration_error_returns_2(self):
        stderr = io.StringIO()
        with patch(
            "atg_pool_report.Settings.load", side_effect=ConfigError("missing .env")
        ):
            code = atg_pool_report.main([], stdout=io.StringIO(), stderr=stderr)

        self.assertEqual(code, 2)
        self.assertIn("missing .env", stderr.getvalue())

    def test_api_error_returns_1_without_traceback(self):
        stderr = io.StringIO()
        with patch("atg_pool_report.Settings.load", return_value=self.settings), patch(
            "atg_pool_report.AtgClient"
        ) as client_type:
            client_type.return_value.login.side_effect = APIError("login failed")
            code = atg_pool_report.main([], stdout=io.StringIO(), stderr=stderr)

        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue().strip(), "错误: login failed")

    def test_incomplete_snapshot_is_saved_and_returns_3(self):
        snapshot = value_snapshot(complete=False)
        with patch("atg_pool_report.Settings.load", return_value=self.settings), patch(
            "atg_pool_report.AtgClient"
        ) as client_type, patch(
            "atg_pool_report.collect_pool", return_value=snapshot
        ), patch(
            "atg_pool_report.build_summary", return_value={}
        ), patch(
            "atg_pool_report.format_terminal_report", return_value="INCOMPLETE"
        ), patch(
            "atg_pool_report.write_reports",
            return_value=(Path("partial.csv"), Path("partial.json")),
        ) as write_reports:
            client_type.return_value.login.return_value = None
            code = atg_pool_report.main(
                [], stdout=io.StringIO(), stderr=io.StringIO()
            )

        self.assertEqual(code, 3)
        write_reports.assert_called_once()

    def test_parser_does_not_offer_secret_arguments(self):
        help_text = atg_pool_report.build_parser().format_help().lower()

        self.assertNotIn("password", help_text)
        self.assertNotIn("session", help_text)
        self.assertNotIn("token", help_text)
        self.assertIn("--sort-type", help_text)

    def test_non_secret_cli_values_are_forwarded_as_overrides(self):
        output_dir = Path(self.temp_dir.name) / "custom"
        snapshot = value_snapshot()
        with patch("atg_pool_report.Settings.load", return_value=self.settings) as load, patch(
            "atg_pool_report.AtgClient"
        ) as client_type, patch(
            "atg_pool_report.collect_pool", return_value=snapshot
        ), patch("atg_pool_report.build_summary", return_value={}), patch(
            "atg_pool_report.format_terminal_report", return_value="OK"
        ), patch(
            "atg_pool_report.write_reports", return_value=(Path("a"), Path("b"))
        ):
            client_type.return_value.login.return_value = None
            atg_pool_report.main(
                [
                    "--sort-type",
                    "2",
                    "--order-type",
                    "5",
                    "--page-size",
                    "25",
                    "--output-dir",
                    str(output_dir),
                ],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        overrides = load.call_args.kwargs["overrides"]
        self.assertEqual(overrides["sort_type"], 2)
        self.assertEqual(overrides["order_type"], 5)
        self.assertEqual(overrides["page_size"], 25)
        self.assertEqual(overrides["report_directory"], output_dir)


if __name__ == "__main__":
    unittest.main()
