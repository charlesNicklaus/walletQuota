from unittest import TestCase

from walletquota.config import AppSettings, ConfigError
from walletquota.models import ProviderResult
from walletquota.providers.millerpay import MillerPayProvider
from walletquota.providers.penguinpay import PenguinPayProvider
from walletquota.registry import UnavailableProvider, build_providers


class StubProvider:
    def __init__(self, name, values, overrides):
        self._name = name
        self.values = values
        self.overrides = overrides

    @property
    def name(self):
        return self._name

    def run(self):
        return ProviderResult.success(self.name, (), {})


def factory(name):
    return lambda values, overrides: StubProvider(name, values, overrides)


class RegistryTests(TestCase):
    def test_new_channels_are_registered_in_configured_order(self):
        settings = AppSettings.from_values({
            "ENABLED_PROVIDERS": "smartwallet,sharkpay",
            "SMARTWALLET_PHONE": "fixture-phone",
            "SMARTWALLET_PASSWORD": "fixture-password",
            "SHARKPAY_PHONE": "fixture-phone",
            "SHARKPAY_PASSWORD": "fixture-password",
        })

        providers = build_providers(settings)

        self.assertEqual([p.name for p in providers], ["smartwallet", "sharkpay"])
        self.assertTrue(all(not isinstance(p, UnavailableProvider) for p in providers))

    def test_default_registry_builds_three_channels_in_configured_order(self):
        settings = AppSettings.from_values(
            {
                "ENABLED_PROVIDERS": "penguinpay,millerpay,atgpay",
                "PENGUINPAY_PHONE": "penguin-phone",
                "PENGUINPAY_PASSWORD": "penguin-secret",
                "MILLERPAY_PHONE": "miller-phone",
                "MILLERPAY_PASSWORD": "miller-secret",
                "ATGPAY_PHONE": "atg-phone",
                "ATGPAY_PASSWORD": "atg-secret",
            }
        )

        providers = build_providers(settings)

        self.assertEqual(
            [provider.name for provider in providers],
            ["penguinpay", "millerpay", "atgpay"],
        )
        self.assertIsInstance(providers[0], PenguinPayProvider)
        self.assertIsInstance(providers[1], MillerPayProvider)

    def test_builds_registered_providers_in_configured_order(self):
        settings = AppSettings.from_values(
            {"ENABLED_PROVIDERS": "xxxpay,atgpay", "SAFE": "value"}
        )
        providers = build_providers(
            settings,
            factories={"atgpay": factory("atgpay"), "xxxpay": factory("xxxpay")},
            overrides={"atgpay": {"page_size": 25}},
        )

        self.assertEqual([provider.name for provider in providers], ["xxxpay", "atgpay"])
        self.assertEqual(providers[1].overrides, {"page_size": 25})
        self.assertEqual(providers[0].values["SAFE"], "value")

    def test_unknown_provider_is_kept_as_safe_error_result(self):
        settings = AppSettings.from_values(
            {"ENABLED_PROVIDERS": "atgpay,unknownpay"}
        )
        providers = build_providers(
            settings, factories={"atgpay": factory("atgpay")}
        )

        self.assertIsInstance(providers[1], UnavailableProvider)
        result = providers[1].run()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.category, "configuration")
        self.assertEqual(result.error.message, "Provider is not registered")

    def test_all_unknown_providers_are_program_configuration_error(self):
        settings = AppSettings.from_values(
            {"ENABLED_PROVIDERS": "unknownpay,anotherpay"}
        )

        with self.assertRaisesRegex(ConfigError, "No enabled provider"):
            build_providers(settings, factories={"atgpay": factory("atgpay")})
