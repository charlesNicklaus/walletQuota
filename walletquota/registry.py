from typing import Any, Callable, Mapping, Optional

from .config import AppSettings, ConfigError
from .models import ProviderResult
from .providers.atgpay import AtgPayProvider
from .providers.millerpay import MillerPayProvider


ProviderFactory = Callable[[Mapping[str, str], Mapping[str, Any]], Any]


PROVIDER_FACTORIES: Mapping[str, ProviderFactory] = {
    "atgpay": lambda values, overrides: AtgPayProvider(
        values, overrides=overrides
    ),
    "millerpay": lambda values, overrides: MillerPayProvider(
        values, overrides=overrides
    ),
}


class UnavailableProvider:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self) -> ProviderResult:
        return ProviderResult.failure(
            self.name, "configuration", "Provider is not registered"
        )


def build_providers(
    settings: AppSettings,
    factories: Mapping[str, ProviderFactory] = PROVIDER_FACTORIES,
    overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
):
    if not any(name in factories for name in settings.enabled_providers):
        raise ConfigError("No enabled provider is registered")

    overrides = overrides or {}
    providers = []
    for name in settings.enabled_providers:
        if name in factories:
            providers.append(
                factories[name](settings.values, overrides.get(name, {}))
            )
        else:
            providers.append(UnavailableProvider(name))
    return tuple(providers)
