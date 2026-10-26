from dataclasses import dataclass, field, replace
import math
from pathlib import Path
from typing import Any, Mapping, Optional

from .spec import SMARTWALLET_SPEC


class ConfigError(ValueError):
    """Raised when SmartWallet configuration is missing or invalid."""


def _integer(values: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(values.get(key, str(default)))
    except (TypeError, ValueError) as error:
        raise ConfigError("{} must be an integer".format(key)) from error


def _number(values: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(values.get(key, str(default)))
    except (TypeError, ValueError) as error:
        raise ConfigError("{} must be a number".format(key)) from error


def _trusted_api_base(value: str) -> str:
    try:
        return SMARTWALLET_SPEC.normalized_api_base(value)
    except ValueError as error:
        raise ConfigError(
            "SMARTWALLET_API_BASE must be the trusted SmartWallet endpoint"
        ) from error


@dataclass(frozen=True)
class SmartWalletSettings:
    phone: str
    password: str = field(repr=False)
    api_base: str = SMARTWALLET_SPEC.api_base
    sort_type: int = SMARTWALLET_SPEC.default_sort_type
    order_type: int = SMARTWALLET_SPEC.default_order_type
    page_size: int = 10
    request_delay_ms: int = 150
    timeout_seconds: float = 30.0
    report_directory: Path = Path("reports")

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "SmartWalletSettings":
        for key in ("SMARTWALLET_PHONE", "SMARTWALLET_PASSWORD"):
            if not values.get(key):
                raise ConfigError("{} is required in configuration".format(key))
        settings = cls(
            phone=values["SMARTWALLET_PHONE"],
            password=values["SMARTWALLET_PASSWORD"],
            api_base=_trusted_api_base(
                values.get("SMARTWALLET_API_BASE", SMARTWALLET_SPEC.api_base)
            ),
            sort_type=_integer(values, "SMARTWALLET_SORT_TYPE", 1),
            order_type=_integer(values, "SMARTWALLET_ORDER_TYPE", 1),
            page_size=_integer(values, "SMARTWALLET_PAGE_SIZE", 10),
            request_delay_ms=_integer(
                values, "SMARTWALLET_REQUEST_DELAY_MS", 150
            ),
            timeout_seconds=_number(values, "SMARTWALLET_TIMEOUT_SECONDS", 30.0),
            report_directory=Path(values.get("REPORT_DIRECTORY", "reports")),
        )
        if overrides:
            allowed = {"sort_type", "order_type", "page_size", "report_directory"}
            selected = {
                key: value
                for key, value in overrides.items()
                if key in allowed and value is not None
            }
            settings = replace(settings, **selected)
        settings.validate()
        return settings

    def validate(self) -> None:
        if not 1 <= self.page_size <= 1000:
            raise ConfigError("SMARTWALLET_PAGE_SIZE must be between 1 and 1000")
        if self.request_delay_ms < 0:
            raise ConfigError("SMARTWALLET_REQUEST_DELAY_MS cannot be negative")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ConfigError("SMARTWALLET_TIMEOUT_SECONDS must be greater than zero")
        _trusted_api_base(self.api_base)
