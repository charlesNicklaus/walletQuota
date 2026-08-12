import tempfile
from pathlib import Path
from unittest import TestCase

from walletquota.config import AppSettings, ConfigError


class AppSettingsTests(TestCase):
    def write_env(self, content: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_normalizes_deduplicates_and_preserves_provider_order(self):
        settings = AppSettings.from_values(
            {"ENABLED_PROVIDERS": "ATGPAY, xxxpay,atgpay"}
        )

        self.assertEqual(settings.enabled_providers, ("atgpay", "xxxpay"))

    def test_absent_provider_setting_defaults_to_atgpay(self):
        settings = AppSettings.from_values({})

        self.assertEqual(settings.enabled_providers, ("atgpay",))

    def test_values_are_immutable_copies_and_hidden_from_repr(self):
        values = {"ATGPAY_PASSWORD": "do-not-print"}
        settings = AppSettings.from_values(values)
        values["ATGPAY_PASSWORD"] = "changed"

        self.assertEqual(settings.values["ATGPAY_PASSWORD"], "do-not-print")
        self.assertNotIn("do-not-print", repr(settings))
        with self.assertRaises(TypeError):
            settings.values["ATGPAY_PASSWORD"] = "changed"

    def test_loads_quotes_and_comments_from_env(self):
        settings = AppSettings.load(
            self.write_env(
                'ENABLED_PROVIDERS="atgpay, xxxpay" # comment\n'
                'ATGPAY_PASSWORD="secret value"\n'
                "REPORT_DIRECTORY=snapshots\n"
            )
        )

        self.assertEqual(settings.enabled_providers, ("atgpay", "xxxpay"))
        self.assertEqual(settings.values["ATGPAY_PASSWORD"], "secret value")
        self.assertEqual(settings.report_directory, Path("snapshots"))

    def test_cli_overrides_enabled_providers_and_output_directory(self):
        settings = AppSettings.from_values(
            {"ENABLED_PROVIDERS": "xxxpay"},
            enabled=("atgpay",),
            output_dir=Path("custom"),
        )

        self.assertEqual(settings.enabled_providers, ("atgpay",))
        self.assertEqual(settings.report_directory, Path("custom"))

    def test_empty_or_invalid_provider_configuration_is_rejected(self):
        cases = (
            {"ENABLED_PROVIDERS": ""},
            {"ENABLED_PROVIDERS": "atg-pay"},
            {"ENABLED_PROVIDERS": "支付"},
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ConfigError):
                    AppSettings.from_values(values)

        with self.assertRaises(ConfigError):
            AppSettings.from_values({}, enabled=())

    def test_empty_report_directory_is_rejected(self):
        with self.assertRaises(ConfigError):
            AppSettings.from_values({"REPORT_DIRECTORY": ""})

    def test_missing_env_file_is_rejected_without_secret_values(self):
        missing = Path("/private/tmp/no-wallet-quota-env")

        with self.assertRaises(ConfigError) as captured:
            AppSettings.load(missing)

        self.assertIn("Configuration file not found", str(captured.exception))
        self.assertNotIn("PASSWORD", str(captured.exception))
