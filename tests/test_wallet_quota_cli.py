import io
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import TestCase, mock

import wallet_quota
from walletquota.models import Order, ProviderResult
from walletquota.reporting import ReportBundle


class StaticProvider:
    def __init__(self, name, result):
        self._name = name
        self.result = result
        self.calls = 0

    @property
    def name(self):
        return self._name

    def run(self):
        self.calls += 1
        return self.result


class WalletQuotaCliTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = self.root / ".env"
        self.env.write_text(
            "ENABLED_PROVIDERS=atgpay,xxxpay\n"
            "ATGPAY_PASSWORD=do-not-print-password\n",
            encoding="utf-8",
        )
        self.bundle = ReportBundle(
            self.root / "report",
            self.root / "report" / "aggregate.json",
            {},
        )

    @staticmethod
    def success_provider(name, amount="10"):
        return StaticProvider(
            name,
            ProviderResult.success(
                name, (Order(name, "A1", Decimal(amount)),), {}
            ),
        )

    def run_main(self, providers, extra_args=()):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            wallet_quota, "build_providers", return_value=tuple(providers)
        ), mock.patch.object(
            wallet_quota, "write_report_bundle", return_value=self.bundle
        ):
            code = wallet_quota.main(
                ["--env-file", str(self.env), *extra_args], stdout, stderr
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_all_success_calls_every_provider_and_returns_zero(self):
        atgpay = self.success_provider("atgpay", "10")
        xxxpay = self.success_provider("xxxpay", "20")

        code, output, errors = self.run_main((atgpay, xxxpay))

        self.assertEqual(code, 0)
        self.assertEqual(atgpay.calls, 1)
        self.assertEqual(xxxpay.calls, 1)
        self.assertIn("total:", output)
        self.assertIn("atgpay: success", output)
        self.assertIn("xxxpay: success", output)
        self.assertIn("综合 JSON:", output)
        self.assertEqual(errors, "")

    def test_channel_error_still_calls_all_and_returns_three(self):
        atgpay = self.success_provider("atgpay")
        xxxpay = StaticProvider(
            "xxxpay",
            ProviderResult.failure("xxxpay", "authentication", "Login failed"),
        )

        code, output, _ = self.run_main((atgpay, xxxpay))

        self.assertEqual(code, 3)
        self.assertEqual(atgpay.calls, 1)
        self.assertEqual(xxxpay.calls, 1)
        self.assertIn("xxxpay: error", output)

    def test_missing_configuration_returns_two(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = wallet_quota.main(
            ["--env-file", str(self.root / "missing.env")], stdout, stderr
        )

        self.assertEqual(code, 2)
        self.assertIn("配置错误", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_report_write_error_returns_one(self):
        provider = self.success_provider("atgpay")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            wallet_quota, "build_providers", return_value=(provider,)
        ), mock.patch.object(
            wallet_quota, "write_report_bundle", side_effect=OSError("disk full")
        ):
            code = wallet_quota.main(
                ["--env-file", str(self.env)], stdout, stderr
            )

        self.assertEqual(code, 1)
        self.assertIn("错误", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_verbose_progress_is_safe(self):
        provider = self.success_provider("atgpay")

        code, _, errors = self.run_main((provider,), ("--verbose",))

        self.assertEqual(code, 0)
        self.assertIn("atgpay", errors)
        self.assertNotIn("do-not-print-password", errors)

    def test_parser_does_not_accept_secret_arguments(self):
        destinations = {action.dest for action in wallet_quota.build_parser()._actions}

        self.assertNotIn("password", destinations)
        self.assertNotIn("phone", destinations)

    def test_new_entry_honors_legacy_report_directory(self):
        legacy_directory = self.root / "legacy-wallet-reports"
        self.env.write_text(
            "ATG_REPORT_DIRECTORY={}\n".format(legacy_directory),
            encoding="utf-8",
        )
        provider = self.success_provider("atgpay")
        with mock.patch.object(
            wallet_quota, "build_providers", return_value=(provider,)
        ), mock.patch.object(
            wallet_quota, "write_report_bundle", return_value=self.bundle
        ) as writer:
            code = wallet_quota.main(
                ["--env-file", str(self.env)], io.StringIO(), io.StringIO()
            )

        self.assertEqual(code, 0)
        self.assertEqual(writer.call_args.args[1], legacy_directory)
