from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)

from .accounts import AccountSnapshot, collect_accounts
from .client import AtgClient
from .config import AtgSettings, ConfigError
from .pool import collect_pool
from .spec import ATGPAY_SPEC


def _collect(settings: AtgSettings) -> CollectionResult:
    client = AtgClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    return snapshot, collect_accounts(client)


class AtgPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[AtgSettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=ATGPAY_SPEC,
            settings_factory=lambda source, selected: AtgSettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="ATG Pay configuration is invalid",
                authentication="ATG Pay authentication failed",
                api="ATG Pay API request failed",
                data="ATG Pay returned invalid order data",
                incomplete="ATG order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
