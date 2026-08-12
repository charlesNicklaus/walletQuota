import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import atg_pool_report
from walletquota.models import Order, ProviderResult
from walletquota.reporting import ReportBundle


class StaticProvider:
    name = "atgpay"

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def run(self):
        self.calls += 1
        return self.result


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.env = self.root / ".env"
        self.env.write_text(
            "ATG_PHONE=9876543210\nATG_PASSWORD=secret\n",
            encoding="utf-8",
        )
        self.bundle = ReportBundle(
            self.root / "bundle",
            self.root / "bundle" / "aggregate.json",
            {"atgpay": self.root / "bundle" / "atgpay_orders.csv"},
        )

    @staticmethod
    def success_result():
        return ProviderResult.success(
            "atgpay",
            (Order("atgpay", "A1", Decimal("10")),),
            {},
        )

    def test_success_prints_aggregate_summary_and_report_paths(self):
        stdout = io.StringIO()
        provider = StaticProvider(self.success_result())
        with patch(
            "atg_pool_report.build_providers", return_value=(provider,)
        ), patch(
            "atg_pool_report.write_report_bundle", return_value=self.bundle
        ):
            code = atg_pool_report.main(
                ["--env-file", str(self.env)], stdout=stdout, stderr=io.StringIO()
            )

        output = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(provider.calls, 1)
        self.assertIn("total:", output)
        self.assertIn("atgpay: success", output)
        self.assertIn(str(self.bundle.aggregate_json), output)
        self.assertIn(str(self.bundle.channel_csv["atgpay"]), output)

    def test_configuration_error_returns_two(self):
        stderr = io.StringIO()

        code = atg_pool_report.main(
            ["--env-file", str(self.root / "missing.env")],
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(code, 2)
        self.assertIn("Configuration file not found", stderr.getvalue())

    def test_channel_error_is_reported_and_returns_three(self):
        stderr = io.StringIO()
        stdout = io.StringIO()
        provider = StaticProvider(
            ProviderResult.failure("atgpay", "api", "ATG Pay API request failed")
        )
        with patch(
            "atg_pool_report.build_providers", return_value=(provider,)
        ), patch(
            "atg_pool_report.write_report_bundle", return_value=self.bundle
        ):
            code = atg_pool_report.main(
                ["--env-file", str(self.env)], stdout=stdout, stderr=stderr
            )

        self.assertEqual(code, 3)
        self.assertIn("atgpay: error", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_partial_result_is_saved_and_returns_three(self):
        provider = StaticProvider(
            ProviderResult.partial(
                "atgpay",
                (Order("atgpay", "A1", Decimal("10")),),
                {},
                "Snapshot incomplete",
            )
        )
        with patch(
            "atg_pool_report.build_providers", return_value=(provider,)
        ), patch(
            "atg_pool_report.write_report_bundle", return_value=self.bundle
        ) as writer:
            code = atg_pool_report.main(
                ["--env-file", str(self.env)],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        self.assertEqual(code, 3)
        writer.assert_called_once()

    def test_parser_does_not_offer_secret_arguments(self):
        help_text = atg_pool_report.build_parser().format_help().lower()

        self.assertNotIn("password", help_text)
        self.assertNotIn("session", help_text)
        self.assertNotIn("token", help_text)
        self.assertIn("--sort-type", help_text)

    def test_non_secret_cli_values_are_forwarded_to_atgpay(self):
        output_dir = self.root / "custom"
        provider = StaticProvider(self.success_result())
        with patch(
            "atg_pool_report.build_providers", return_value=(provider,)
        ) as build, patch(
            "atg_pool_report.write_report_bundle", return_value=self.bundle
        ) as writer:
            atg_pool_report.main(
                [
                    "--env-file",
                    str(self.env),
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

        overrides = build.call_args.kwargs["overrides"]["atgpay"]
        self.assertEqual(overrides["sort_type"], 2)
        self.assertEqual(overrides["order_type"], 5)
        self.assertEqual(overrides["page_size"], 25)
        self.assertEqual(writer.call_args.args[1], output_dir)


if __name__ == "__main__":
    unittest.main()
