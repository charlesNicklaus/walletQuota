import time
from typing import Callable, Optional

from walletquota.providers.signed_pool.client import (
    APIError,
    AuthenticationError,
    HTTPStatusError,
    SignedPoolClient,
    Transport,
    TransportError,
    UrllibTransport,
    make_signature,
)

from .config import SmartWalletSettings
from .spec import SMARTWALLET_SPEC


class SmartWalletClient(SignedPoolClient):
    def __init__(
        self,
        settings: SmartWalletSettings,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        super().__init__(
            settings,
            SMARTWALLET_SPEC,
            transport=transport,
            sleep=sleep,
            clock_ms=clock_ms,
        )


__all__ = [
    "APIError",
    "AuthenticationError",
    "HTTPStatusError",
    "SmartWalletClient",
    "Transport",
    "TransportError",
    "UrllibTransport",
    "make_signature",
]
