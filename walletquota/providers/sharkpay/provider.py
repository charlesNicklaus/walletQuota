from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)
from walletquota.providers.signed_pool.pool import collect_pool

from .client import SharkPayClient
from .config import ConfigError, SharkPaySettings
from .spec import SHARKPAY_SPEC


def _collect(settings: SharkPaySettings) -> CollectionResult:
    client = SharkPayClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    accounts = collect_accounts(client, SHARKPAY_SPEC)
    return snapshot, accounts


class SharkPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[SharkPaySettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=SHARKPAY_SPEC,
            settings_factory=lambda source, selected: SharkPaySettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="SharkPay configuration is invalid",
                authentication="SharkPay authentication failed",
                api="SharkPay API request failed",
                data="SharkPay returned invalid order data",
                incomplete="SharkPay order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
