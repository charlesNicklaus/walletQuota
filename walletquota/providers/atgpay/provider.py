from typing import Any, Callable, Mapping, Optional

from walletquota.models import Order, ProviderResult

from .client import APIError, AtgClient, AuthenticationError
from .config import AtgSettings, ConfigError
from .pool import PoolDataError, PoolSnapshot, collect_pool


def _collect(settings: AtgSettings) -> PoolSnapshot:
    client = AtgClient(settings)
    client.login()
    return collect_pool(client, settings)


class AtgPayProvider:
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[AtgSettings], PoolSnapshot] = _collect,
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
    def _metadata(snapshot: PoolSnapshot):
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
        }

    def run(self) -> ProviderResult:
        try:
            self.settings = AtgSettings.from_values(
                self._values, overrides=self._overrides
            )
            snapshot = self._collect(self.settings)
            orders = self._orders(snapshot)
            metadata = self._metadata(snapshot)
            if snapshot.complete:
                return ProviderResult.success(self.name, orders, metadata)
            return ProviderResult.partial(
                self.name, orders, metadata, "ATG order-pool snapshot is incomplete"
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
