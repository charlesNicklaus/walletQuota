from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


ACCOUNT_KINDS = frozenset(
    {"bound_upi", "bank_account", "order_payee_upi", "order_payee_bank"}
)


@dataclass(frozen=True)
class Account:
    provider: str
    kind: str
    account_id: str
    account: Optional[str] = None
    upi: Optional[str] = None
    bank_name: Optional[str] = None
    ifsc: Optional[str] = None
    order_id: Optional[str] = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider or not self.account_id:
            raise ValueError("Account provider and account_id are required")
        if self.kind not in ACCOUNT_KINDS:
            raise ValueError("Invalid account kind")
        if self.kind in {"bound_upi", "order_payee_upi"} and not self.upi:
            raise ValueError("UPI account kinds require a UPI")
        if self.kind in {"bank_account", "order_payee_bank"} and not self.account:
            raise ValueError("Bank account kinds require an account")
        object.__setattr__(self, "attributes", _immutable_mapping(self.attributes))


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
    accounts: Tuple[Account, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("Provider name is required")
        if self.status not in {"success", "partial", "error"}:
            raise ValueError("Invalid provider status")
        if self.status == "success" and not self.complete:
            raise ValueError("A successful provider result must be complete")
        if self.status != "success" and self.error is None:
            raise ValueError("A non-success provider result requires an error")
        if self.status == "error" and self.accounts:
            raise ValueError("An error provider result cannot contain accounts")
        if any(order.provider != self.provider for order in self.orders):
            raise ValueError("Provider result contains an order from another provider")
        if any(account.provider != self.provider for account in self.accounts):
            raise ValueError("Provider result contains an account from another provider")
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "accounts", tuple(self.accounts))
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    @classmethod
    def success(
        cls,
        provider: str,
        orders: Tuple[Order, ...],
        metadata: Mapping[str, Any],
        accounts: Tuple[Account, ...] = (),
    ) -> "ProviderResult":
        return cls(provider, "success", tuple(orders), True, metadata, None, accounts)

    @classmethod
    def partial(
        cls,
        provider: str,
        orders: Tuple[Order, ...],
        metadata: Mapping[str, Any],
        message: str,
        accounts: Tuple[Account, ...] = (),
    ) -> "ProviderResult":
        return cls(
            provider,
            "partial",
            tuple(orders),
            False,
            metadata,
            ProviderError("incomplete", message),
            accounts,
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
