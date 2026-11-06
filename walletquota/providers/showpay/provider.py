from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)
from walletquota.providers.signed_pool.pool import collect_pool

from .client import ShowPayClient
from .config import ConfigError, ShowPaySettings
from .spec import SHOWPAY_SPEC


def _collect(settings: ShowPaySettings) -> CollectionResult:
    client = ShowPayClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    accounts = collect_accounts(client, SHOWPAY_SPEC)
    return snapshot, accounts


class ShowPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[ShowPaySettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=SHOWPAY_SPEC,
            settings_factory=ShowPaySettings.from_values,
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="ShowPay configuration is invalid",
                authentication="ShowPay authentication failed",
                api="ShowPay API request failed",
                data="ShowPay returned invalid order data",
                incomplete="ShowPay order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
