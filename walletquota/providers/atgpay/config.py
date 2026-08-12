from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


class ConfigError(ValueError):
    """Raised when local configuration is missing or invalid."""


def _strip_inline_comment(value: str) -> str:
    quote: Optional[str] = None
    result = []
    for index, character in enumerate(value):
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
        if character == "#" and quote is None:
            if index == 0 or value[index - 1].isspace():
                break
        result.append(character)
    return "".join(result).strip()


def _parse_env(path: Path) -> Dict[str, str]:
    if not path.is_file():
        raise ConfigError("Configuration file not found: {}".format(path))

    values: Dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError("Invalid .env line {}".format(line_number))
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(raw_value)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _integer(values: Mapping[str, str], key: str, default: int) -> int:
    raw = values.get(key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError) as error:
        raise ConfigError("{} must be an integer".format(key)) from error


def _number(values: Mapping[str, str], key: str, default: float) -> float:
    raw = values.get(key, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError) as error:
        raise ConfigError("{} must be a number".format(key)) from error


@dataclass(frozen=True)
class AtgSettings:
    phone: str
    password: str = field(repr=False)
    api_base: str = "https://api.atg-game.com/app"
    sort_type: int = 1
    order_type: int = 4
    page_size: int = 10
    request_delay_ms: int = 150
    timeout_seconds: float = 30.0
    report_directory: Path = Path("reports")

    @classmethod
    def load(
        cls,
        path: Path,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "AtgSettings":
        return cls.from_values(
            _parse_env(Path(path)), overrides=overrides, source=str(path)
        )

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        source: str = "configuration",
    ) -> "AtgSettings":
        values = _prefer_channel_prefix(values)
        for key in ("ATG_PHONE", "ATG_PASSWORD"):
            if not values.get(key):
                raise ConfigError("{} is required in {}".format(key, source))

        settings = cls(
            phone=values["ATG_PHONE"],
            password=values["ATG_PASSWORD"],
            api_base=values.get(
                "ATG_API_BASE", "https://api.atg-game.com/app"
            ).rstrip("/"),
            sort_type=_integer(values, "ATG_SORT_TYPE", 1),
            order_type=_integer(values, "ATG_ORDER_TYPE", 4),
            page_size=_integer(values, "ATG_PAGE_SIZE", 10),
            request_delay_ms=_integer(values, "ATG_REQUEST_DELAY_MS", 150),
            timeout_seconds=_number(values, "ATG_TIMEOUT_SECONDS", 30.0),
            report_directory=Path(values.get("ATG_REPORT_DIRECTORY", "reports")),
        )
        if overrides:
            allowed = {
                "sort_type",
                "order_type",
                "page_size",
                "report_directory",
            }
            clean = {
                key: value
                for key, value in overrides.items()
                if key in allowed and value is not None
            }
            settings = replace(settings, **clean)
        settings.validate()
        return settings

    def validate(self) -> None:
        if not 1 <= self.page_size <= 1000:
            raise ConfigError("ATG_PAGE_SIZE must be between 1 and 1000")
        if self.request_delay_ms < 0:
            raise ConfigError("ATG_REQUEST_DELAY_MS cannot be negative")
        if self.timeout_seconds <= 0:
            raise ConfigError("ATG_TIMEOUT_SECONDS must be greater than zero")
        if not self.api_base.startswith("https://"):
            raise ConfigError("ATG_API_BASE must use https://")


def _prefer_channel_prefix(values: Mapping[str, str]) -> Dict[str, str]:
    normalized = dict(values)
    suffixes = (
        "PHONE",
        "PASSWORD",
        "API_BASE",
        "SORT_TYPE",
        "ORDER_TYPE",
        "PAGE_SIZE",
        "REQUEST_DELAY_MS",
        "TIMEOUT_SECONDS",
        "REPORT_DIRECTORY",
    )
    for suffix in suffixes:
        new_key = "ATGPAY_{}".format(suffix)
        if new_key in values:
            normalized["ATG_{}".format(suffix)] = values[new_key]
    return normalized


Settings = AtgSettings
