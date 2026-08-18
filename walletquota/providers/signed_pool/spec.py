import re
from dataclasses import dataclass
from typing import FrozenSet
from urllib.parse import urlsplit


READ_ONLY_PATHS = frozenset(
    {
        "/auth/login",
        "/pay/debit/task",
        "/user/upi/bind/list",
        "/bank/debit/card/list",
        "/pay/my/task",
        "/pay/order/detail",
    }
)
_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _trusted_endpoint(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path.rstrip("/") == "/app"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _trusted_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _matches_endpoint_origin(api_base: str, web_origin: str) -> bool:
    api_host = urlsplit(api_base).hostname
    origin_host = urlsplit(web_origin).hostname
    return (
        api_host is not None
        and api_host.startswith("api.")
        and origin_host == "app-web." + api_host[len("api.") :]
    )


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    display_name: str
    api_label: str
    api_base: str
    web_origin: str
    user_agent: str
    default_sort_type: int
    default_order_type: int
    allowed_paths: FrozenSet[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_base", self.api_base.rstrip("/"))
        object.__setattr__(self, "web_origin", self.web_origin.rstrip("/"))
        object.__setattr__(self, "allowed_paths", frozenset(self.allowed_paths))
        if not _PROVIDER_NAME.fullmatch(self.name):
            raise ValueError("Invalid provider name")
        if not _trusted_endpoint(self.api_base):
            raise ValueError("Channel must use a trusted API endpoint")
        if not _trusted_origin(self.web_origin) or not _matches_endpoint_origin(
            self.api_base, self.web_origin
        ):
            raise ValueError("Channel must use a trusted web origin")
        if self.allowed_paths != READ_ONLY_PATHS:
            raise ValueError("Channel must use the exact read-only allowlist")

    def url_for(self, configured_api_base: str, path: str) -> str:
        configured = configured_api_base.rstrip("/")
        if not _trusted_endpoint(configured) or configured != self.api_base:
            raise ValueError("Channel must use its trusted API endpoint")
        if path not in self.allowed_paths:
            raise ValueError("Unsupported API endpoint")
        return "{}{}".format(configured, path)
