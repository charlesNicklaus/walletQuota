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

from .config import MillerPaySettings
from .spec import MILLERPAY_SPEC


class MillerPayClient(SignedPoolClient):
    def __init__(
        self,
        settings: MillerPaySettings,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        super().__init__(
            settings,
            MILLERPAY_SPEC,
            transport=transport,
            sleep=sleep,
            clock_ms=clock_ms,
        )


__all__ = [
    "APIError",
    "AuthenticationError",
    "HTTPStatusError",
    "MillerPayClient",
    "Transport",
    "TransportError",
    "UrllibTransport",
    "make_signature",
]
