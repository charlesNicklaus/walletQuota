from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)
from walletquota.providers.signed_pool.pool import collect_pool

from .client import PenguinPayClient
from .config import ConfigError, PenguinPaySettings
from .spec import PENGUINPAY_SPEC


def _collect(settings: PenguinPaySettings) -> CollectionResult:
    client = PenguinPayClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    accounts = collect_accounts(client, PENGUINPAY_SPEC)
    return snapshot, accounts


class PenguinPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[PenguinPaySettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=PENGUINPAY_SPEC,
            settings_factory=lambda source, selected: PenguinPaySettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="PenguinPay configuration is invalid",
                authentication="PenguinPay authentication failed",
                api="PenguinPay API request failed",
                data="PenguinPay returned invalid order data",
                incomplete="PenguinPay order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
