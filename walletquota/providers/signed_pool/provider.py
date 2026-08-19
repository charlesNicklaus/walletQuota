from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple, Type, Union

from walletquota.models import Order, ProviderResult

from .accounts import AccountSnapshot
from .client import APIError, AuthenticationError
from .pool import PoolDataError, PoolSnapshot
from .spec import ChannelSpec


CollectionResult = Union[PoolSnapshot, Tuple[PoolSnapshot, AccountSnapshot]]


@dataclass(frozen=True)
class ProviderMessages:
    configuration: str
    authentication: str
    api: str
    data: str
    incomplete: str


class SignedPoolProvider:
    def __init__(
        self,
        values: Mapping[str, str],
        spec: ChannelSpec,
        settings_factory: Callable[
            [Mapping[str, str], Optional[Mapping[str, Any]]], Any
        ],
        config_error: Type[Exception],
        messages: ProviderMessages,
        collect: Callable[[Any], CollectionResult],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._values = dict(values)
        self._spec = spec
        self._settings_factory = settings_factory
        self._config_error = config_error
        self._messages = messages
        self._collect = collect
        self._overrides = dict(overrides or {})
        self.settings = None

    @property
    def name(self) -> str:
        return self._spec.name

    def _orders(self, snapshot: PoolSnapshot) -> Tuple[Order, ...]:
        return tuple(
            Order(
                provider=self.name,
                order_id=source.order_no,
                amount=source.order_amount,
                income=source.income,
                balance=source.balance,
                attributes={
                    "inr_recharge_rate": source.inr_recharge_rate,
                    "inr_recharge_fee": source.inr_recharge_fee,
                    "vip": source.vip,
                },
            )
            for source in snapshot.orders
        )

    def _metadata(
        self, snapshot: PoolSnapshot, accounts: AccountSnapshot
    ) -> Mapping[str, Any]:
        return {
            "started_at": snapshot.started_at.isoformat(),
            "finished_at": snapshot.finished_at.isoformat(),
            "pages_fetched": snapshot.pages_fetched,
            "observed_totals": list(snapshot.observed_totals),
            "duplicate_count": snapshot.duplicate_count,
            "stable": snapshot.stable,
            "stop_reason": snapshot.stop_reason,
            "sort_type": snapshot.sort_type,
            "order_type": snapshot.order_type,
            "inr_today_buy_count": snapshot.inr_today_buy_count,
            "bound_upi_total": accounts.bound_upi_total,
            "bank_card_total": accounts.bank_card_total,
            "claimed_task_total": accounts.claimed_task_total,
            "account_warnings": self._safe_account_warnings(accounts.warnings),
        }

    def _safe_account_warnings(self, warnings) -> list:
        suffixes = {
            "account failed",
            "bound UPI account collection failed",
            "bank card account collection failed",
            "claimed task collection failed",
            "claimed task pagination is inconsistent",
            "claimed task page limit reached",
            "claimed order detail collection failed",
        }
        allowed = {
            "{} {}".format(self._spec.api_label, suffix) for suffix in suffixes
        }
        fallback = "{} account collection failed".format(self._spec.api_label)
        safe = []
        for warning in warnings:
            prefix = str(warning).partition(":")[0]
            value = prefix if prefix in allowed else fallback
            if value not in safe:
                safe.append(value)
        return safe

    def run(self) -> ProviderResult:
        try:
            self.settings = self._settings_factory(self._values, self._overrides)
            collected = self._collect(self.settings)
            if isinstance(collected, tuple):
                snapshot, accounts = collected
            else:
                snapshot, accounts = collected, AccountSnapshot.empty()
            orders = self._orders(snapshot)
            metadata = self._metadata(snapshot, accounts)
            if snapshot.complete:
                return ProviderResult.success(
                    self.name, orders, metadata, accounts.accounts
                )
            return ProviderResult.partial(
                self.name,
                orders,
                metadata,
                self._messages.incomplete,
                accounts.accounts,
            )
        except self._config_error:
            return ProviderResult.failure(
                self.name, "configuration", self._messages.configuration
            )
        except AuthenticationError:
            return ProviderResult.failure(
                self.name, "authentication", self._messages.authentication
            )
        except APIError:
            return ProviderResult.failure(self.name, "api", self._messages.api)
        except PoolDataError:
            return ProviderResult.failure(self.name, "data", self._messages.data)
