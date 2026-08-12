import tempfile
import unittest
from pathlib import Path

from atg_pool.config import ConfigError, Settings


class SettingsTests(unittest.TestCase):
    def write_env(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_required_values_comments_and_quotes(self):
        path = self.write_env(
            """
            # local credentials
            ATG_PHONE='9876543210'
            ATG_PASSWORD="secret value"
            ATG_PAGE_SIZE=50 # inline comment
            ATG_REQUEST_DELAY_MS=0
            """
        )

        settings = Settings.load(path)

        self.assertEqual(settings.phone, "9876543210")
        self.assertEqual(settings.password, "secret value")
        self.assertEqual(settings.page_size, 50)
        self.assertEqual(settings.request_delay_ms, 0)
        self.assertEqual(settings.sort_type, 1)
        self.assertEqual(settings.order_type, 4)

    def test_missing_required_value_is_rejected(self):
        path = self.write_env("ATG_PHONE=9876543210\n")

        with self.assertRaisesRegex(ConfigError, "ATG_PASSWORD"):
            Settings.load(path)

    def test_invalid_integer_is_rejected(self):
        path = self.write_env(
            "ATG_PHONE=9876543210\nATG_PASSWORD=secret\nATG_PAGE_SIZE=many\n"
        )

        with self.assertRaisesRegex(ConfigError, "ATG_PAGE_SIZE"):
            Settings.load(path)

    def test_out_of_range_page_size_is_rejected(self):
        path = self.write_env(
            "ATG_PHONE=9876543210\nATG_PASSWORD=secret\nATG_PAGE_SIZE=1001\n"
        )

        with self.assertRaisesRegex(ConfigError, "between 1 and 1000"):
            Settings.load(path)

    def test_repr_does_not_expose_password(self):
        path = self.write_env("ATG_PHONE=9876543210\nATG_PASSWORD=do-not-print\n")

        representation = repr(Settings.load(path))

        self.assertNotIn("do-not-print", representation)
        self.assertNotIn("password=", representation)

    def test_non_secret_overrides_replace_environment_defaults(self):
        path = self.write_env("ATG_PHONE=9876543210\nATG_PASSWORD=secret\n")

        settings = Settings.load(
            path,
            overrides={"sort_type": 2, "order_type": 7, "page_size": 25},
        )

        self.assertEqual(settings.sort_type, 2)
        self.assertEqual(settings.order_type, 7)
        self.assertEqual(settings.page_size, 25)

    def test_default_page_size_matches_confirmed_request(self):
        path = self.write_env("ATG_PHONE=9876543210\nATG_PASSWORD=secret\n")

        settings = Settings.load(path)

        self.assertEqual(settings.page_size, 10)

    def test_only_the_allowlisted_atg_api_base_is_accepted(self):
        accepted = self.write_env(
            "ATG_PHONE=9876543210\n"
            "ATG_PASSWORD=secret\n"
            "ATG_API_BASE=https://api.atg-game.com/app/\n"
        )

        self.assertEqual(
            Settings.load(accepted).api_base,
            "https://api.atg-game.com/app",
        )

    def test_rejects_untrusted_or_ambiguous_atg_api_bases(self):
        values = (
            "https://attacker.example/app",
            "https://api.atg-game.com.evil.example/app",
            "https://api.atg-game.com@attacker.example/app",
            "https://user:password@api.atg-game.com/app",
            "https://api.atg-game.com:8443/app",
            "https://api.atg-game.com/collect",
            "https://api.atg-game.com/app?redirect=evil",
            "https://api.atg-game.com/app#fragment",
            "http://api.atg-game.com/app",
        )
        for api_base in values:
            with self.subTest(api_base=api_base):
                path = self.write_env(
                    "ATG_PHONE=9876543210\n"
                    "ATG_PASSWORD=secret\n"
                    "ATG_API_BASE={}\n".format(api_base)
                )

                with self.assertRaisesRegex(ConfigError, "trusted ATG endpoint"):
                    Settings.load(path)


if __name__ == "__main__":
    unittest.main()
