from typing import Any, Callable, Mapping, Optional, Tuple, Union

from walletquota.models import Order, ProviderResult

from .accounts import AccountSnapshot, collect_accounts
from .client import APIError, AtgClient, AuthenticationError
from .config import AtgSettings, ConfigError
from .pool import PoolDataError, PoolSnapshot, collect_pool


CollectionResult = Union[PoolSnapshot, Tuple[PoolSnapshot, AccountSnapshot]]


def _collect(settings: AtgSettings) -> CollectionResult:
    client = AtgClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    return snapshot, collect_accounts(client)


class AtgPayProvider:
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[AtgSettings], CollectionResult] = _collect,
    ) -> None:
        self._values = dict(values)
        self._overrides = dict(overrides or {})
        self._collect = collect
        self.settings: Optional[AtgSettings] = None

    @property
    def name(self) -> str:
        return "atgpay"

    @staticmethod
    def _orders(snapshot: PoolSnapshot):
        return tuple(
            Order(
                provider="atgpay",
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

    @staticmethod
    def _metadata(snapshot: PoolSnapshot, accounts: AccountSnapshot):
        metadata = {
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
            "account_warnings": AtgPayProvider._safe_account_warnings(
                accounts.warnings
            ),
        }
        return metadata

    @staticmethod
    def _safe_account_warnings(warnings):
        allowed = {
            "ATG account failed",
            "ATG bound UPI account collection failed",
            "ATG bank card account collection failed",
            "ATG claimed task collection failed",
            "ATG claimed task pagination is inconsistent",
            "ATG claimed task page limit reached",
            "ATG claimed order detail collection failed",
        }
        safe = []
        for warning in warnings:
            prefix = str(warning).partition(":")[0]
            value = prefix if prefix in allowed else "ATG account collection failed"
            if value not in safe:
                safe.append(value)
        return safe

    def run(self) -> ProviderResult:
        try:
            self.settings = AtgSettings.from_values(
                self._values, overrides=self._overrides
            )
            collected = self._collect(self.settings)
            if isinstance(collected, tuple):
                snapshot, account_snapshot = collected
            else:
                snapshot = collected
                account_snapshot = AccountSnapshot.empty()
            orders = self._orders(snapshot)
            metadata = self._metadata(snapshot, account_snapshot)
            if snapshot.complete:
                return ProviderResult.success(
                    self.name, orders, metadata, account_snapshot.accounts
                )
            return ProviderResult.partial(
                self.name,
                orders,
                metadata,
                "ATG order-pool snapshot is incomplete",
                account_snapshot.accounts,
            )
        except ConfigError:
            return ProviderResult.failure(
                self.name, "configuration", "ATG Pay configuration is invalid"
            )
        except AuthenticationError:
            return ProviderResult.failure(
                self.name, "authentication", "ATG Pay authentication failed"
            )
        except APIError:
            return ProviderResult.failure(
                self.name, "api", "ATG Pay API request failed"
            )
        except PoolDataError:
            return ProviderResult.failure(
                self.name, "data", "ATG Pay returned invalid order data"
            )
