from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class Order:
    provider: str
    order_id: str
    amount: Decimal
    currency: str = "INR"
    income: Optional[Decimal] = None
    balance: Optional[Decimal] = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider or not self.order_id:
            raise ValueError("Order provider and order_id are required")
        for value in (self.amount, self.income, self.balance):
            if value is not None and not value.is_finite():
                raise ValueError("Order monetary values must be finite")
        if self.currency != "INR":
            raise ValueError("Only INR orders are supported")
        object.__setattr__(self, "attributes", _immutable_mapping(self.attributes))


@dataclass(frozen=True)
class ProviderError:
    category: str
    message: str


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    status: str
    orders: Tuple[Order, ...]
    complete: bool
    metadata: Mapping[str, Any]
    error: Optional[ProviderError] = None

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("Provider name is required")
        if self.status not in {"success", "partial", "error"}:
            raise ValueError("Invalid provider status")
        if self.status == "success" and not self.complete:
            raise ValueError("A successful provider result must be complete")
        if self.status != "success" and self.error is None:
            raise ValueError("A non-success provider result requires an error")
        if any(order.provider != self.provider for order in self.orders):
            raise ValueError("Provider result contains an order from another provider")
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    @classmethod
    def success(
        cls,
        provider: str,
        orders: Tuple[Order, ...],
        metadata: Mapping[str, Any],
    ) -> "ProviderResult":
        return cls(provider, "success", tuple(orders), True, metadata)

    @classmethod
    def partial(
        cls,
        provider: str,
        orders: Tuple[Order, ...],
        metadata: Mapping[str, Any],
        message: str,
    ) -> "ProviderResult":
        return cls(
            provider,
            "partial",
            tuple(orders),
            False,
            metadata,
            ProviderError("incomplete", message),
        )

    @classmethod
    def failure(
        cls, provider: str, category: str, message: str
    ) -> "ProviderResult":
        return cls(
            provider,
            "error",
            (),
            False,
            {},
            ProviderError(category, message),
        )


@dataclass(frozen=True)
class AggregateResult:
    status: str
    channels: Tuple[ProviderResult, ...]
    total: Mapping[str, Any]
    started_at: datetime
    finished_at: datetime

    def __post_init__(self) -> None:
        if self.status not in {"success", "partial"}:
            raise ValueError("Invalid aggregate status")
        object.__setattr__(self, "channels", tuple(self.channels))
        object.__setattr__(self, "total", _immutable_mapping(self.total))
