import re
from dataclasses import dataclass
from typing import FrozenSet, Optional
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
_TRUSTED_PROVIDER_HOSTS = {
    "atgpay": ("api.atg-game.com", "app-web.atg-game.com"),
    "millerpay": ("api.millerpay-app.com", "app-web.millerpay-app.com"),
    "penguinpay": ("api.penguinpay-app.com", "app-web.penguinpay-app.com"),
}


def _normalized_endpoint(value: str) -> Optional[str]:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path.rstrip("/") == "/app"
            and not parsed.query
            and not parsed.fragment
        ):
            return "https://{}/app".format(parsed.hostname)
    except ValueError:
        pass
    return None


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
        normalized_api_base = _normalized_endpoint(self.api_base)
        object.__setattr__(
            self,
            "api_base",
            (
                normalized_api_base
                if normalized_api_base is not None
                else self.api_base.rstrip("/")
            ),
        )
        object.__setattr__(self, "web_origin", self.web_origin.rstrip("/"))
        object.__setattr__(self, "allowed_paths", frozenset(self.allowed_paths))
        if not _PROVIDER_NAME.fullmatch(self.name):
            raise ValueError("Invalid provider name")
        trusted_hosts = _TRUSTED_PROVIDER_HOSTS.get(self.name)
        if (
            trusted_hosts is None
            or normalized_api_base is None
            or urlsplit(self.api_base).hostname != trusted_hosts[0]
        ):
            raise ValueError("Channel must use a trusted API endpoint")
        if (
            not _trusted_origin(self.web_origin)
            or urlsplit(self.web_origin).hostname != trusted_hosts[1]
        ):
            raise ValueError("Channel must use a trusted web origin")
        if self.allowed_paths != READ_ONLY_PATHS:
            raise ValueError("Channel must use the exact read-only allowlist")

    def normalized_api_base(self, configured_api_base: str) -> str:
        configured = _normalized_endpoint(configured_api_base)
        if configured is None or configured != self.api_base:
            raise ValueError("Channel must use its trusted API endpoint")
        return configured

    def url_for(self, configured_api_base: str, path: str) -> str:
        configured = self.normalized_api_base(configured_api_base)
        if path not in self.allowed_paths:
            raise ValueError("Unsupported API endpoint")
        return "{}{}".format(configured, path)
