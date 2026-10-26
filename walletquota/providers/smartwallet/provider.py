from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)
from walletquota.providers.signed_pool.pool import collect_pool

from .client import SmartWalletClient
from .config import ConfigError, SmartWalletSettings
from .spec import SMARTWALLET_SPEC


def _collect(settings: SmartWalletSettings) -> CollectionResult:
    client = SmartWalletClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    accounts = collect_accounts(client, SMARTWALLET_SPEC)
    return snapshot, accounts


class SmartWalletProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[SmartWalletSettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=SMARTWALLET_SPEC,
            settings_factory=lambda source, selected: SmartWalletSettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="SmartWallet configuration is invalid",
                authentication="SmartWallet authentication failed",
                api="SmartWallet API request failed",
                data="SmartWallet returned invalid order data",
                incomplete="SmartWallet order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
