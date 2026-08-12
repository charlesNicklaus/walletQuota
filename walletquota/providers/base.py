from typing import Protocol

from walletquota.models import ProviderResult


class Provider(Protocol):
    @property
    def name(self) -> str:
        ...

    def run(self) -> ProviderResult:
        ...
