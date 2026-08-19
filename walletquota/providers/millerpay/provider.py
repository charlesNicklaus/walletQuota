from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)
from walletquota.providers.signed_pool.pool import collect_pool

from .client import MillerPayClient
from .config import ConfigError, MillerPaySettings
from .spec import MILLERPAY_SPEC


def _collect(settings: MillerPaySettings) -> CollectionResult:
    client = MillerPayClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    accounts = collect_accounts(client, MILLERPAY_SPEC)
    return snapshot, accounts


class MillerPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[MillerPaySettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=MILLERPAY_SPEC,
            settings_factory=lambda source, selected: MillerPaySettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="MillerPay configuration is invalid",
                authentication="MillerPay authentication failed",
                api="MillerPay API request failed",
                data="MillerPay returned invalid order data",
                incomplete="MillerPay order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
