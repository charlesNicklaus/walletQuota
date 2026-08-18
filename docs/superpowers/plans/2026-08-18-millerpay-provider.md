# MillerPay Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MillerPay as a second read-only order-pool provider used by both manual and 20-minute scheduled runs, while extracting the protocol it shares with ATGPay into a secure reusable core.

**Architecture:** Introduce a `walletquota.providers.signed_pool` package for the confirmed login/signature/session, pagination, account collection, and standard-result behavior. Keep ATGPay and MillerPay as thin channel adapters with separate immutable specifications, settings, trusted hosts, defaults, and public provider classes; the existing registry, runner, aggregate, reporting, scheduler, and retention layers continue to consume `ProviderResult` without channel-specific branches.

**Tech Stack:** Python 3.9+ standard library, `unittest`, `urllib.request`, `Decimal`, dataclasses, existing walletQuota models/reporting/scheduler.

## Global Constraints

- Keep Python compatibility at 3.9 or newer and add no third-party dependency.
- All order currency remains `INR`; use finite `Decimal` values for money.
- The only network paths are exactly `/auth/login`, `/pay/debit/task`, `/user/upi/bind/list`, `/bank/debit/card/list`, `/pay/my/task`, and `/pay/order/detail`.
- Never call `/pay/receive/task`, payment, UTR, bind, delete, update, or any other mutation endpoint.
- Validate HTTPS, exact trusted API host, `/app` base path, no credentials in URLs, and exact read-path allowlists before transport.
- Passwords, cookies, tokens, session keys, signatures, authorization headers, and raw authentication responses must never enter logs or reports.
- Terminal account values remain masked; complete accounts remain only in the local same-day `aggregate.json` and never in channel CSV files.
- Keep the ATGPay provider name, public imports, legacy `ATG_*` configuration, `ATGPAY_*` precedence, default `sortType=1`, and default `orderType=4` unchanged.
- MillerPay uses provider name `millerpay`, API base `https://api.millerpay-app.com/app`, Origin `https://app-web.millerpay-app.com`, default `sortType=1`, and default `orderType=1`.
- A channel failure is isolated by the existing runner; persistent authentication failures are provider authentication errors, while non-authentication account enrichment failures are fixed safe warnings.
- Manual and scheduler execution must share `ENABLED_PROVIDERS`; do not add scheduler-specific provider logic.
- Do not change the IST cleanup ownership marker, report-root containment checks, symlink/path protections, or deletion scope.
- Unit and end-to-end tests use fake transports and local temporary directories only. A real smoke test is a final, explicitly read-only verification after automated tests.
- Never add or stage the real `.env`, credentials, generated `reports/`, scheduler lock files, or Firecrawl artifacts.

---

### Task 1: Immutable signed-pool channel specification

**Files:**
- Create: `walletquota/providers/signed_pool/__init__.py`
- Create: `walletquota/providers/signed_pool/spec.py`
- Create: `walletquota/providers/atgpay/spec.py`
- Create: `tests/test_signed_pool_spec.py`

**Interfaces:**
- Consumes: no new internal interface; use only standard-library dataclasses, regex, and `urllib.parse.urlsplit`.
- Produces: `READ_ONLY_PATHS: FrozenSet[str]`, `ChannelSpec`, `ChannelSpec.url_for(configured_api_base: str, path: str) -> str`, and `ATGPAY_SPEC` for the shared client and channel adapters.

- [ ] **Step 1: Write the failing channel-specification tests**

Create `tests/test_signed_pool_spec.py` with exact allowlist, immutable normalization, trusted-base, Origin, and write-path rejection coverage:

```python
from dataclasses import FrozenInstanceError, replace
from unittest import TestCase

from walletquota.providers.signed_pool.spec import (
    READ_ONLY_PATHS,
    ChannelSpec,
)


def spec():
    return ChannelSpec(
        name="millerpay",
        display_name="MillerPay",
        api_label="MillerPay",
        api_base="https://api.millerpay-app.com/app",
        web_origin="https://app-web.millerpay-app.com",
        user_agent="WalletQuota/millerpay/1.0",
        default_sort_type=1,
        default_order_type=1,
        allowed_paths=READ_ONLY_PATHS,
    )


class ChannelSpecTests(TestCase):
    def test_exact_read_only_allowlist_and_url_resolution(self):
        channel = spec()
        self.assertEqual(
            channel.allowed_paths,
            frozenset(
                {
                    "/auth/login",
                    "/pay/debit/task",
                    "/user/upi/bind/list",
                    "/bank/debit/card/list",
                    "/pay/my/task",
                    "/pay/order/detail",
                }
            ),
        )
        self.assertEqual(
            channel.url_for(channel.api_base, "/pay/debit/task"),
            "https://api.millerpay-app.com/app/pay/debit/task",
        )

    def test_rejects_write_path_before_url_is_returned(self):
        with self.assertRaisesRegex(ValueError, "Unsupported API endpoint"):
            spec().url_for(spec().api_base, "/pay/receive/task")

    def test_rejects_untrusted_configured_base(self):
        for value in (
            "http://api.millerpay-app.com/app",
            "https://evil.example/app",
            "https://api.millerpay-app.com/app/extra",
            "https://user:password@api.millerpay-app.com/app",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "trusted API endpoint"
            ):
                spec().url_for(value, "/auth/login")

    def test_rejects_invalid_origin_and_non_exact_allowlist(self):
        with self.assertRaisesRegex(ValueError, "trusted web origin"):
            replace(spec(), web_origin="https://evil.example")
        with self.assertRaisesRegex(ValueError, "exact read-only allowlist"):
            replace(spec(), allowed_paths=frozenset({"/auth/login"}))

    def test_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            spec().name = "other"
```

- [ ] **Step 2: Run the specification tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_signed_pool_spec -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'walletquota.providers.signed_pool'`.

- [ ] **Step 3: Implement the immutable channel specification**

Create `walletquota/providers/signed_pool/spec.py` with the following public surface and validations:

```python
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
        if not _trusted_origin(self.web_origin):
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
```

Create `walletquota/providers/signed_pool/__init__.py`:

```python
"""Shared read-only protocol for the confirmed signed order-pool channels."""

from .spec import ChannelSpec, READ_ONLY_PATHS

__all__ = ["ChannelSpec", "READ_ONLY_PATHS"]
```

Create `walletquota/providers/atgpay/spec.py`:

```python
from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


ATGPAY_SPEC = ChannelSpec(
    name="atgpay",
    display_name="ATG Pay",
    api_label="ATG",
    api_base="https://api.atg-game.com/app",
    web_origin="https://app-web.atg-game.com",
    user_agent="ATGPoolReport/1.0",
    default_sort_type=1,
    default_order_type=4,
    allowed_paths=READ_ONLY_PATHS,
)
```

- [ ] **Step 4: Run focused and existing ATG configuration tests**

Run:

```bash
python3 -m unittest tests.test_signed_pool_spec tests.test_config -v
```

Expected: all tests PASS and output ends with `OK`.

- [ ] **Step 5: Commit the channel specification**

```bash
git add walletquota/providers/signed_pool/__init__.py walletquota/providers/signed_pool/spec.py walletquota/providers/atgpay/spec.py tests/test_signed_pool_spec.py
git commit -m "refactor: define signed pool channel contract"
```

---

### Task 2: Shared signed client with ATG compatibility wrapper

**Files:**
- Create: `walletquota/providers/signed_pool/client.py`
- Modify: `walletquota/providers/atgpay/client.py`
- Create: `tests/test_signed_pool_client.py`
- Verify unchanged: `atg_pool/client.py`
- Verify unchanged: `tests/test_client.py`

**Interfaces:**
- Consumes: `ChannelSpec.url_for`, settings attributes `phone`, `password`, `api_base`, and `timeout_seconds`.
- Produces: `SignedPoolClient`, `Transport`, `UrllibTransport`, `APIError`, `AuthenticationError`, `TransportError`, `HTTPStatusError`, and `make_signature`; `AtgClient` remains importable from both `walletquota.providers.atgpay.client` and `atg_pool.client`.

- [ ] **Step 1: Write failing shared-client tests**

Create `tests/test_signed_pool_client.py`. Reuse a local `ScriptedTransport` and construct a `ChannelSpec` for MillerPay. Cover these exact assertions:

```python
from types import SimpleNamespace
from unittest import TestCase

from walletquota.providers.signed_pool.client import (
    APIError,
    AuthenticationError,
    HTTPStatusError,
    SignedPoolClient,
    make_signature,
)
from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


MILLER_SPEC = ChannelSpec(
    "millerpay",
    "MillerPay",
    "MillerPay",
    "https://api.millerpay-app.com/app",
    "https://app-web.millerpay-app.com",
    "WalletQuota/millerpay/1.0",
    1,
    1,
    READ_ONLY_PATHS,
)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, payload, headers, timeout):
        self.calls.append((url, dict(payload), dict(headers), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def settings():
    return SimpleNamespace(
        phone="test-phone",
        password="test-password",
        api_base=MILLER_SPEC.api_base,
        timeout_seconds=12.0,
    )


def login(session_key="session-one"):
    return {
        "code": 200,
        "message": "success",
        "data": {
            "userId": 17,
            "loginToken": "token",
            "sessionKey": session_key,
        },
    }


def success(**data):
    return {"code": 200, "message": "success", "data": data}


class SignedPoolClientTests(TestCase):
    def test_uses_channel_origin_url_and_unsigned_login(self):
        transport = ScriptedTransport([login()])
        client = SignedPoolClient(settings(), MILLER_SPEC, transport=transport)
        client.login()
        url, payload, headers, timeout = transport.calls[0]
        self.assertEqual(url, MILLER_SPEC.api_base + "/auth/login")
        self.assertEqual(payload, {"phone": "test-phone", "password": "test-password"})
        self.assertEqual(headers["Origin"], MILLER_SPEC.web_origin)
        self.assertEqual(headers["Referer"], MILLER_SPEC.web_origin + "/")
        self.assertNotIn("Signature", headers)
        self.assertEqual(timeout, 12.0)

    def test_transport_retry_rebuilds_strictly_increasing_timestamp_and_signature(self):
        transport = ScriptedTransport(
            [login(), HTTPStatusError(503), HTTPStatusError(429, 0), success(total=0, list=[])]
        )
        sleeps = []
        client = SignedPoolClient(
            settings(),
            MILLER_SPEC,
            transport=transport,
            sleep=sleeps.append,
            clock_ms=lambda: 100,
        )
        client.fetch_pool_page(1, 10, 1, 1)
        pool_calls = transport.calls[1:]
        self.assertEqual([item[1]["ts"] for item in pool_calls], [100, 101, 102])
        self.assertEqual(len({item[2]["Signature"] for item in pool_calls}), 3)
        for unused_url, payload, headers, unused_timeout in pool_calls:
            self.assertEqual(headers["Signature"], make_signature("session-one", payload))
        self.assertEqual(sleeps, [0.5, 0])

    def test_auth_code_relogs_once_and_resigns_with_new_session(self):
        transport = ScriptedTransport(
            [
                login("one"),
                {"code": 1002, "message": "expired", "data": {}},
                login("two"),
                success(total=0, list=[]),
            ]
        )
        client = SignedPoolClient(
            settings(), MILLER_SPEC, transport=transport, clock_ms=lambda: 100
        )
        client.fetch_pool_page(1, 10, 1, 1)
        retry_payload = transport.calls[3][1]
        retry_headers = transport.calls[3][2]
        self.assertEqual(retry_payload["ts"], 101)
        self.assertEqual(retry_headers["Signature"], make_signature("two", retry_payload))

    def test_persistent_auth_failure_is_bounded_and_safe(self):
        transport = ScriptedTransport(
            [
                login("one"),
                {"code": 1001, "message": "raw secret", "data": {}},
                login("two"),
                {"code": 1001, "message": "raw secret", "data": {}},
            ]
        )
        client = SignedPoolClient(settings(), MILLER_SPEC, transport=transport)
        with self.assertRaisesRegex(AuthenticationError, "MillerPay authentication failed"):
            client.fetch_pool_page(1, 10, 1, 1)
        self.assertEqual(len(transport.calls), 4)

    def test_write_path_and_wrong_base_fail_before_transport(self):
        transport = ScriptedTransport([])
        client = SignedPoolClient(settings(), MILLER_SPEC, transport=transport)
        with self.assertRaisesRegex(APIError, "Unsupported API endpoint"):
            client._url("/pay/receive/task")
        client.settings.api_base = "https://evil.example/app"
        with self.assertRaisesRegex(APIError, "trusted API endpoint"):
            client._url("/auth/login")
        self.assertEqual(transport.calls, [])
```

- [ ] **Step 2: Run the shared-client tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_signed_pool_client -v
```

Expected: FAIL because `walletquota.providers.signed_pool.client` does not exist.

- [ ] **Step 3: Move and parameterize the confirmed client implementation**

Create `walletquota/providers/signed_pool/client.py` by moving the current ATG implementation and making these exact substitutions:

```python
class SignedPoolClient:
    LOGIN_PATH = "/auth/login"
    POOL_PATH = "/pay/debit/task"
    BOUND_UPIS_PATH = "/user/upi/bind/list"
    BANK_CARDS_PATH = "/bank/debit/card/list"
    MY_TASKS_PATH = "/pay/my/task"
    ORDER_DETAIL_PATH = "/pay/order/detail"
    MAX_TRANSIENT_RETRIES = 2

    def __init__(
        self,
        settings,
        spec: ChannelSpec,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.settings = settings
        self.spec = spec
        self.transport = transport or UrllibTransport()
        self.sleep = sleep
        self.clock_ms = clock_ms
        self.user_id: Optional[int] = None
        self.login_token: Optional[str] = None
        self.session_key: Optional[str] = None

    @property
    def base_headers(self) -> Mapping[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": self.spec.web_origin,
            "Referer": self.spec.web_origin + "/",
            "User-Agent": self.spec.user_agent,
        }

    def _url(self, path: str) -> str:
        try:
            return self.spec.url_for(self.settings.api_base, path)
        except ValueError as error:
            raise APIError(str(error)) from error

    def _data_from_envelope(
        self, response: Mapping[str, Any], authentication: bool = False
    ) -> Mapping[str, Any]:
        code = response.get("code")
        if code != 200:
            if code in {1001, 1002} or authentication:
                raise AuthenticationError(
                    "{} authentication failed".format(self.spec.api_label)
                )
            message = str(response.get("message") or "API request failed")
            raise APIError(message)
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise APIError("{} API response is missing data".format(self.spec.api_label))
        return data
```

Keep the current, already tested implementations of `make_signature`, `_post_with_retry`, `login`, `_signed_read`, `fetch_pool_page`, `fetch_bound_upis`, `fetch_bank_cards`, `fetch_my_tasks_page`, and `fetch_order_detail`. In `_signed_read`, retain `last_timestamp` outside the two-attempt session loop and retain `timestamp = max(clock_ms(), last_timestamp + 1)` so both transport retries and re-login retries are strictly newer. `UrllibTransport` must continue using a private CookieJar and must raise only fixed generic transport messages.

Export the exact public names:

```python
__all__ = [
    "APIError",
    "AuthenticationError",
    "HTTPStatusError",
    "SignedPoolClient",
    "Transport",
    "TransportError",
    "UrllibTransport",
    "make_signature",
]
```

- [ ] **Step 4: Replace ATG client implementation with a compatibility subclass**

Replace `walletquota/providers/atgpay/client.py` with:

```python
import time
from typing import Callable, Optional

from walletquota.providers.signed_pool.client import (
    APIError,
    AuthenticationError,
    HTTPStatusError,
    SignedPoolClient,
    Transport,
    TransportError,
    UrllibTransport,
    make_signature,
)

from .config import AtgSettings
from .spec import ATGPAY_SPEC


class AtgClient(SignedPoolClient):
    def __init__(
        self,
        settings: AtgSettings,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        super().__init__(
            settings,
            ATGPAY_SPEC,
            transport=transport,
            sleep=sleep,
            clock_ms=clock_ms,
        )


__all__ = [
    "APIError",
    "AtgClient",
    "AuthenticationError",
    "HTTPStatusError",
    "Transport",
    "TransportError",
    "UrllibTransport",
    "make_signature",
]
```

Do not modify `atg_pool/client.py`; its existing wildcard re-export must continue to expose the names above.

- [ ] **Step 5: Run shared and full ATG client regression tests**

Run:

```bash
python3 -m unittest tests.test_signed_pool_client tests.test_client -v
```

Expected: all tests PASS, including exact ATG Origin, error text, retry timestamps, strict allowlist, and all four account reads.

- [ ] **Step 6: Commit the shared client extraction**

```bash
git add walletquota/providers/signed_pool/client.py walletquota/providers/atgpay/client.py tests/test_signed_pool_client.py
git commit -m "refactor: share signed pool API client"
```

---

### Task 3: Provider-neutral order-pool parsing and pagination

**Files:**
- Create: `walletquota/providers/signed_pool/pool.py`
- Modify: `walletquota/providers/atgpay/pool.py`
- Create: `tests/test_signed_pool_pool.py`
- Verify unchanged: `atg_pool/pool.py`
- Verify unchanged: `tests/test_pool.py`

**Interfaces:**
- Consumes: any client implementing `fetch_pool_page(page, size, sort_type, order_type)`, and settings attributes `page_size`, `sort_type`, `order_type`, and `request_delay_ms`.
- Produces: `PoolDataError`, source `Order`, `PoolSnapshot`, and `collect_pool(client, settings, sleep, now, max_pages) -> PoolSnapshot`.

- [ ] **Step 1: Write failing Miller-shaped pool tests**

Create `tests/test_signed_pool_pool.py` with a `PageClient` and `SimpleNamespace` settings. The primary test must use MillerPay’s missing optional fields and `orderType=1`:

```python
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from walletquota.providers.signed_pool.pool import PoolDataError, collect_pool


class PageClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetch_pool_page(self, page, size, sort_type, order_type):
        self.calls.append((page, size, sort_type, order_type))
        return self.pages.pop(0)


def settings():
    return SimpleNamespace(
        page_size=1,
        sort_type=1,
        order_type=1,
        request_delay_ms=0,
    )


class SignedPoolCollectionTests(TestCase):
    def test_collects_miller_pages_with_optional_fee_and_vip_absent(self):
        client = PageClient(
            [
                {
                    "total": 2,
                    "list": [
                        {
                            "orderNo": "M-1",
                            "orderAmount": 522,
                            "income": 18.66,
                            "balance": 540.66,
                            "inrRechargeRate": 3,
                        }
                    ],
                },
                {
                    "total": 2,
                    "list": [{"orderNo": "M-2", "orderAmount": "10000"}],
                },
            ]
        )
        snapshot = collect_pool(client, settings(), sleep=lambda unused: None)
        self.assertEqual(client.calls, [(1, 1, 1, 1), (2, 1, 1, 1)])
        self.assertEqual([order.order_no for order in snapshot.orders], ["M-1", "M-2"])
        self.assertEqual(snapshot.orders[1].order_amount, Decimal("10000"))
        self.assertIsNone(snapshot.orders[0].inr_recharge_fee)
        self.assertIsNone(snapshot.orders[0].vip)
        self.assertTrue(snapshot.complete)

    def test_rejects_negative_and_non_finite_amounts(self):
        for amount in ("-1", "NaN", "Infinity"):
            with self.subTest(amount=amount):
                client = PageClient(
                    [{"total": 1, "list": [{"orderNo": "M-1", "orderAmount": amount}]}]
                )
                with self.assertRaisesRegex(PoolDataError, "orderAmount"):
                    collect_pool(client, settings(), sleep=lambda unused: None)
```

- [ ] **Step 2: Run the pool tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_signed_pool_pool -v
```

Expected: FAIL because `walletquota.providers.signed_pool.pool` does not exist.

- [ ] **Step 3: Extract and harden the shared pool collector**

Move the existing contents of `walletquota/providers/atgpay/pool.py` into `walletquota/providers/signed_pool/pool.py`. Remove the ATG settings import and use structural settings access. Keep all existing dataclass field names so ATG callers remain compatible.

Tighten the required amount validation so negative values are rejected while optional income/balance/rate/fee remain optional finite decimals:

```python
def _decimal(value: Any, field_name: str, non_negative: bool = False) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PoolDataError("Invalid {} in order data".format(field_name)) from error
    if not decimal_value.is_finite() or (non_negative and decimal_value < 0):
        raise PoolDataError("Invalid {} in order data".format(field_name))
    return decimal_value


class Order:
    @classmethod
    def from_api(cls, value: Mapping[str, Any]) -> "Order":
        order_no = value.get("orderNo")
        if order_no is None or not str(order_no).strip():
            raise PoolDataError("Order data is missing orderNo")
        if "orderAmount" not in value:
            raise PoolDataError("Order data is missing orderAmount")
        return cls(
            order_no=str(order_no),
            order_amount=_decimal(value["orderAmount"], "orderAmount", non_negative=True),
            income=_optional_decimal(value.get("income"), "income"),
            balance=_optional_decimal(value.get("balance"), "balance"),
            inr_recharge_rate=_optional_decimal(value.get("inrRechargeRate"), "inrRechargeRate"),
            inr_recharge_fee=_optional_decimal(value.get("inrRechargeFee"), "inrRechargeFee"),
            vip=value.get("vip"),
        )
```

Retain the exact pagination invariants already covered by `tests/test_pool.py`: ordered channel-local de-duplication, all observed totals, `stable`, `complete`, empty/short/page-limit stop reasons, and sleeping only between pages.

At the bottom, export:

```python
__all__ = ["Order", "PoolDataError", "PoolSnapshot", "collect_pool"]
```

- [ ] **Step 4: Replace the ATG pool module with a compatibility re-export**

Replace `walletquota/providers/atgpay/pool.py` with:

```python
from walletquota.providers.signed_pool.pool import (
    Order,
    PoolDataError,
    PoolSnapshot,
    collect_pool,
)

__all__ = ["Order", "PoolDataError", "PoolSnapshot", "collect_pool"]
```

Do not modify `atg_pool/pool.py`; it must continue re-exporting the ATG compatibility module.

- [ ] **Step 5: Run focused and legacy pool tests**

Run:

```bash
python3 -m unittest tests.test_signed_pool_pool tests.test_pool tests.test_end_to_end.ConfirmedResponseContractTests -v
```

Expected: all tests PASS. The existing ATG fixtures contain only non-negative amounts, so no legacy test modification is expected.

- [ ] **Step 6: Commit the shared pool extraction**

```bash
git add walletquota/providers/signed_pool/pool.py walletquota/providers/atgpay/pool.py tests/test_signed_pool_pool.py
git commit -m "refactor: share order pool pagination"
```

---

### Task 4: Provider-neutral account collection

**Files:**
- Create: `walletquota/providers/signed_pool/accounts.py`
- Modify: `walletquota/providers/atgpay/accounts.py`
- Create: `tests/test_signed_pool_accounts.py`
- Verify unchanged: `tests/test_atg_accounts.py`

**Interfaces:**
- Consumes: `ChannelSpec`, `AuthenticationError`, the existing immutable `walletquota.models.Account`, and a client implementing the four account read methods.
- Produces: `AccountSnapshot` and `collect_accounts(client, spec, task_page_size=100) -> AccountSnapshot`; ATG’s wrapper preserves `collect_accounts(client, task_page_size=100)`.

- [ ] **Step 1: Write failing provider-neutral account tests**

Create `tests/test_signed_pool_accounts.py` with a fake client returning one UPI, one bank account, one claimed task, and one detail. Assert:

```python
snapshot = collect_accounts(client, MILLER_SPEC, task_page_size=1)
self.assertEqual({account.provider for account in snapshot.accounts}, {"millerpay"})
self.assertEqual(
    [account.kind for account in snapshot.accounts],
    ["bound_upi", "bank_account", "order_payee_upi", "order_payee_bank"],
)
self.assertNotIn("sessionKey", repr(snapshot))
```

Add a derived-ID isolation test using the same account data with `MILLER_SPEC` and `ATGPAY_SPEC`:

```python
self.assertNotEqual(miller.accounts[0].account_id, atg.accounts[0].account_id)
```

Add four subtests that raise `AuthenticationError` from bound UPI, cards, my tasks, and order detail; each must propagate. Add ordinary `RuntimeError("raw-secret")` enrichment failures and assert only these fixed MillerPay warnings are returned:

```python
(
    "MillerPay bound UPI account collection failed",
    "MillerPay bank card account collection failed",
    "MillerPay claimed task collection failed",
)
```

- [ ] **Step 2: Run the account tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_signed_pool_accounts -v
```

Expected: FAIL because `walletquota.providers.signed_pool.accounts` does not exist.

- [ ] **Step 3: Extract account collection with explicit channel context**

Move the current ATG account collector to `walletquota/providers/signed_pool/accounts.py` and make the provider identity explicit in every helper. The important signatures are:

```python
def _server_or_derived_id(
    provider: str,
    value: Mapping[str, Any],
    kind: str,
    account: str = "",
    upi: str = "",
    order_id: str = "",
) -> str:
    server_id = _text(value.get("id"))
    if server_id:
        return server_id
    canonical = "\x1f".join((provider, kind, account, upi, order_id))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _warning(spec: ChannelSpec, suffix: str) -> str:
    return "{} {}".format(spec.api_label, suffix)


def collect_accounts(
    client: Any,
    spec: ChannelSpec,
    task_page_size: int = 100,
) -> AccountSnapshot:
    if (
        isinstance(task_page_size, bool)
        or not isinstance(task_page_size, int)
        or not 1 <= task_page_size <= 1000
    ):
        raise ValueError("task page size must be between 1 and 1000")
```

Thread `spec` through `_bound_upi_accounts`, `_bank_card_accounts`, `_order_detail_accounts`, and `_claimed_order_ids`. Every `Account(...)` must use `spec.name`; every derived ID must include `spec.name`; every fixed warning and safe validation error must use `spec.api_label`. Retain scalar-only field extraction and the exact allowlists already proven by `tests/test_atg_accounts.py`.

For every enrichment call, keep this exception ordering:

```python
try:
    value = client.fetch_bound_upis(1)
except AuthenticationError:
    raise
except Exception:
    _warn_once(warnings, _warning(spec, "bound UPI account collection failed"))
```

Apply the same ordering to cards, claimed-task pages, and order details. Never append exception text or raw response data to warnings.

- [ ] **Step 4: Replace ATG accounts with a compatibility wrapper**

Replace `walletquota/providers/atgpay/accounts.py` with:

```python
from typing import Any

from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.accounts import collect_accounts as _collect_accounts

from .spec import ATGPAY_SPEC


def collect_accounts(client: Any, task_page_size: int = 100) -> AccountSnapshot:
    return _collect_accounts(client, ATGPAY_SPEC, task_page_size=task_page_size)


__all__ = ["AccountSnapshot", "collect_accounts"]
```

- [ ] **Step 5: Run shared and ATG account regressions**

Run:

```bash
python3 -m unittest tests.test_signed_pool_accounts tests.test_atg_accounts -v
```

Expected: all tests PASS, including ATG’s existing deterministic IDs and exact warning text.

- [ ] **Step 6: Commit the shared account collector**

```bash
git add walletquota/providers/signed_pool/accounts.py walletquota/providers/atgpay/accounts.py tests/test_signed_pool_accounts.py
git commit -m "refactor: share read-only account collection"
```

---

### Task 5: Shared standard-result provider with ATG compatibility

**Files:**
- Create: `walletquota/providers/signed_pool/provider.py`
- Modify: `walletquota/providers/atgpay/provider.py`
- Create: `tests/test_signed_pool_provider.py`
- Verify unchanged: `walletquota/providers/atgpay/__init__.py`
- Verify unchanged: `tests/test_atg_provider.py`

**Interfaces:**
- Consumes: `ChannelSpec`, `PoolSnapshot`, `AccountSnapshot`, channel settings factory, channel collection callable, and channel-local configuration exception class.
- Produces: `ProviderMessages`, `SignedPoolProvider`, and retained `AtgPayProvider(values, overrides=None, collect=_collect)`.

- [ ] **Step 1: Write failing generic-provider tests**

Create `tests/test_signed_pool_provider.py` using small local snapshots. Cover:

```python
provider = SignedPoolProvider(
    values={"PHONE": "x", "PASSWORD": "secret"},
    spec=MILLER_SPEC,
    settings_factory=settings_factory,
    config_error=FakeConfigError,
    messages=ProviderMessages(
        configuration="MillerPay configuration is invalid",
        authentication="MillerPay authentication failed",
        api="MillerPay API request failed",
        data="MillerPay returned invalid order data",
        incomplete="MillerPay order-pool snapshot is incomplete",
    ),
    collect=lambda settings: (pool_snapshot, account_snapshot),
)
result = provider.run()
self.assertEqual(result.provider, "millerpay")
self.assertEqual(result.orders[0].provider, "millerpay")
self.assertEqual(result.accounts[0].provider, "millerpay")
self.assertEqual(result.metadata["order_type"], 1)
```

Also assert incomplete snapshots become `partial`, configuration/authentication/API/data exceptions get fixed categories and fixed messages, raw exception strings are absent, and unknown warning strings are normalized to `MillerPay account collection failed`.

- [ ] **Step 2: Run the provider tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_signed_pool_provider -v
```

Expected: FAIL because `walletquota.providers.signed_pool.provider` does not exist.

- [ ] **Step 3: Implement the shared ProviderResult adapter**

Create `walletquota/providers/signed_pool/provider.py` with:

```python
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
        settings_factory: Callable[[Mapping[str, str], Optional[Mapping[str, Any]]], Any],
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
```

Implement `_orders` by copying the current ATG mapping but use `provider=self.name`. Implement `_metadata` with the current exact keys. Implement `_safe_account_warnings` with a generated exact allowlist based on `self._spec.api_label`:

```python
suffixes = {
    "account failed",
    "bound UPI account collection failed",
    "bank card account collection failed",
    "claimed task collection failed",
    "claimed task pagination is inconsistent",
    "claimed task page limit reached",
    "claimed order detail collection failed",
}
allowed = {"{} {}".format(self._spec.api_label, suffix) for suffix in suffixes}
fallback = "{} account collection failed".format(self._spec.api_label)
```

For each warning, keep only `str(warning).partition(":")[0]` when it is in `allowed`; otherwise use `fallback`; de-duplicate without changing order.

Implement `run()` with this exact categorization order:

```python
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
            return ProviderResult.success(self.name, orders, metadata, accounts.accounts)
        return ProviderResult.partial(
            self.name, orders, metadata, self._messages.incomplete, accounts.accounts
        )
    except self._config_error:
        return ProviderResult.failure(self.name, "configuration", self._messages.configuration)
    except AuthenticationError:
        return ProviderResult.failure(self.name, "authentication", self._messages.authentication)
    except APIError:
        return ProviderResult.failure(self.name, "api", self._messages.api)
    except PoolDataError:
        return ProviderResult.failure(self.name, "data", self._messages.data)
```

- [ ] **Step 4: Convert AtgPayProvider to a thin adapter**

Keep `_collect(settings)` exactly as the runtime seam that constructs `AtgClient`, logs in, collects the pool, then collects accounts. Replace the class body with a subclass that wires ATG settings and fixed messages:

```python
class AtgPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[AtgSettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=ATGPAY_SPEC,
            settings_factory=lambda source, selected: AtgSettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="ATG Pay configuration is invalid",
                authentication="ATG Pay authentication failed",
                api="ATG Pay API request failed",
                data="ATG Pay returned invalid order data",
                incomplete="ATG order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
```

Preserve the module-level `CollectionResult` alias and `_collect` for test injection compatibility.

- [ ] **Step 5: Run generic and full ATG provider regressions**

Run:

```bash
python3 -m unittest tests.test_signed_pool_provider tests.test_atg_provider -v
```

Expected: all tests PASS, including legacy configuration precedence, pool-only injection, fixed warning normalization, and persistent account-authentication categorization.

- [ ] **Step 6: Commit the provider extraction**

```bash
git add walletquota/providers/signed_pool/provider.py walletquota/providers/atgpay/provider.py tests/test_signed_pool_provider.py
git commit -m "refactor: share signed pool provider mapping"
```

---

### Task 6: MillerPay settings, client adapter, provider, and registration

**Files:**
- Create: `walletquota/providers/millerpay/__init__.py`
- Create: `walletquota/providers/millerpay/spec.py`
- Create: `walletquota/providers/millerpay/config.py`
- Create: `walletquota/providers/millerpay/client.py`
- Create: `walletquota/providers/millerpay/provider.py`
- Modify: `walletquota/registry.py`
- Create: `tests/test_miller_config.py`
- Create: `tests/test_miller_provider.py`
- Modify: `tests/test_registry.py`

**Interfaces:**
- Consumes: all shared signed-pool interfaces from Tasks 1–5 and existing `build_providers` factory contract.
- Produces: `MILLERPAY_SPEC`, `MillerPaySettings`, `MillerPayClient`, `MillerPayProvider`, and registry key `millerpay`.

- [ ] **Step 1: Write failing MillerPay configuration tests**

Create `tests/test_miller_config.py` covering required credentials, defaults, allowed non-secret overrides, numeric ranges, trusted endpoint, and password `repr` masking:

```python
from unittest import TestCase

from walletquota.providers.millerpay.config import ConfigError, MillerPaySettings


class MillerPaySettingsTests(TestCase):
    def test_defaults_match_confirmed_deposit_contract(self):
        value = MillerPaySettings.from_values(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"}
        )
        self.assertEqual(value.api_base, "https://api.millerpay-app.com/app")
        self.assertEqual((value.sort_type, value.order_type, value.page_size), (1, 1, 10))
        self.assertEqual(value.request_delay_ms, 150)
        self.assertEqual(value.timeout_seconds, 30.0)
        self.assertNotIn("secret", repr(value))

    def test_requires_both_credentials(self):
        for values in ({}, {"MILLERPAY_PHONE": "phone"}, {"MILLERPAY_PASSWORD": "secret"}):
            with self.subTest(values=tuple(values)), self.assertRaises(ConfigError):
                MillerPaySettings.from_values(values)

    def test_rejects_untrusted_api_base(self):
        values = {
            "MILLERPAY_PHONE": "phone",
            "MILLERPAY_PASSWORD": "secret",
            "MILLERPAY_API_BASE": "https://evil.example/app",
        }
        with self.assertRaisesRegex(ConfigError, "trusted MillerPay endpoint"):
            MillerPaySettings.from_values(values)
```

- [ ] **Step 2: Write failing MillerPay provider and registry tests**

Create `tests/test_miller_provider.py` with a deterministic complete snapshot and account snapshot:

```python
from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from walletquota.models import Account
from walletquota.providers.millerpay.provider import MillerPayProvider
from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.client import APIError, AuthenticationError
from walletquota.providers.signed_pool.pool import Order, PoolDataError, PoolSnapshot


def complete_snapshot(complete=True):
    now = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
    return PoolSnapshot(
        orders=(
            Order(
                order_no="MP-1",
                order_amount=Decimal("522"),
                income=Decimal("18.66"),
                balance=Decimal("540.66"),
                inr_recharge_rate=Decimal("3"),
                inr_recharge_fee=None,
                vip=None,
            ),
        ),
        observed_totals=(1 if complete else 2,),
        pages_fetched=1,
        duplicate_count=0,
        complete=complete,
        stable=True,
        stop_reason="reached_total" if complete else "empty_page",
        sort_type=1,
        order_type=1,
        started_at=now,
        finished_at=now,
        inr_today_buy_count=0,
    )


def account_snapshot():
    return AccountSnapshot(
        accounts=(
            Account("millerpay", "bound_upi", "upi-1", upi="local-test@upi"),
        ),
        warnings=(),
        bound_upi_total=1,
        bank_card_total=0,
        claimed_task_total=0,
    )


class MillerPayProviderTests(TestCase):
    def test_maps_complete_snapshot_and_uses_confirmed_defaults(self):
        captured = []
        provider = MillerPayProvider(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"},
            collect=lambda settings: captured.append(settings)
            or (complete_snapshot(), account_snapshot()),
        )
        result = provider.run()
        self.assertEqual((provider.name, result.provider, result.status), ("millerpay", "millerpay", "success"))
        self.assertEqual((captured[0].sort_type, captured[0].order_type), (1, 1))
        self.assertEqual(result.orders[0].provider, "millerpay")
        self.assertIsNone(result.orders[0].attributes["inr_recharge_fee"])
        self.assertIsNone(result.orders[0].attributes["vip"])
        self.assertEqual(result.accounts[0].provider, "millerpay")

    def test_incomplete_snapshot_remains_partial(self):
        provider = MillerPayProvider(
            {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"},
            collect=lambda settings: complete_snapshot(False),
        )
        self.assertEqual(provider.run().status, "partial")

    def test_expected_errors_are_fixed_and_do_not_leak_raw_text(self):
        cases = (
            (AuthenticationError("raw-auth"), "authentication"),
            (APIError("raw-api"), "api"),
            (PoolDataError("raw-data"), "data"),
        )
        for error, category in cases:
            with self.subTest(category=category):
                provider = MillerPayProvider(
                    {"MILLERPAY_PHONE": "phone", "MILLERPAY_PASSWORD": "secret"},
                    collect=lambda settings, raised=error: (_ for _ in ()).throw(raised),
                )
                result = provider.run()
                self.assertEqual(result.error.category, category)
                self.assertNotIn("raw-", result.error.message)

    def test_missing_credentials_is_configuration_error(self):
        result = MillerPayProvider({}, collect=lambda settings: complete_snapshot()).run()
        self.assertEqual(result.error.category, "configuration")
```

Add to `tests/test_registry.py`:

```python
def test_default_registry_builds_atgpay_and_millerpay_in_configured_order(self):
    settings = AppSettings.from_values(
        {
            "ENABLED_PROVIDERS": "millerpay,atgpay",
            "MILLERPAY_PHONE": "miller-phone",
            "MILLERPAY_PASSWORD": "miller-secret",
            "ATGPAY_PHONE": "atg-phone",
            "ATGPAY_PASSWORD": "atg-secret",
        }
    )
    providers = build_providers(settings)
    self.assertEqual([provider.name for provider in providers], ["millerpay", "atgpay"])
```

- [ ] **Step 3: Run MillerPay tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_miller_config tests.test_miller_provider tests.test_registry -v
```

Expected: FAIL because `walletquota.providers.millerpay` is not implemented and `millerpay` is not registered.

- [ ] **Step 4: Implement MillerPay channel specification and settings**

Create `walletquota/providers/millerpay/spec.py`:

```python
from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


MILLERPAY_SPEC = ChannelSpec(
    name="millerpay",
    display_name="MillerPay",
    api_label="MillerPay",
    api_base="https://api.millerpay-app.com/app",
    web_origin="https://app-web.millerpay-app.com",
    user_agent="WalletQuota/millerpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)
```

Create `walletquota/providers/millerpay/config.py` with the complete channel-local loader below. It accepts only MillerPay credential/filter keys, masks the password in `repr`, and never includes a rejected value in an error:

```python
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional

from .spec import MILLERPAY_SPEC


class ConfigError(ValueError):
    """Raised when MillerPay configuration is missing or invalid."""


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


@dataclass(frozen=True)
class MillerPaySettings:
    phone: str
    password: str = field(repr=False)
    api_base: str = MILLERPAY_SPEC.api_base
    sort_type: int = MILLERPAY_SPEC.default_sort_type
    order_type: int = MILLERPAY_SPEC.default_order_type
    page_size: int = 10
    request_delay_ms: int = 150
    timeout_seconds: float = 30.0
    report_directory: Path = Path("reports")

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "MillerPaySettings":
        for key in ("MILLERPAY_PHONE", "MILLERPAY_PASSWORD"):
            if not values.get(key):
                raise ConfigError("{} is required in configuration".format(key))
        settings = cls(
            phone=values["MILLERPAY_PHONE"],
            password=values["MILLERPAY_PASSWORD"],
            api_base=values.get("MILLERPAY_API_BASE", MILLERPAY_SPEC.api_base).rstrip("/"),
            sort_type=_integer(values, "MILLERPAY_SORT_TYPE", 1),
            order_type=_integer(values, "MILLERPAY_ORDER_TYPE", 1),
            page_size=_integer(values, "MILLERPAY_PAGE_SIZE", 10),
            request_delay_ms=_integer(values, "MILLERPAY_REQUEST_DELAY_MS", 150),
            timeout_seconds=_number(values, "MILLERPAY_TIMEOUT_SECONDS", 30.0),
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
            raise ConfigError("MILLERPAY_PAGE_SIZE must be between 1 and 1000")
        if self.request_delay_ms < 0:
            raise ConfigError("MILLERPAY_REQUEST_DELAY_MS cannot be negative")
        if self.timeout_seconds <= 0:
            raise ConfigError("MILLERPAY_TIMEOUT_SECONDS must be greater than zero")
        if self.api_base.rstrip("/") != MILLERPAY_SPEC.api_base:
            raise ConfigError("MILLERPAY_API_BASE must be the trusted MillerPay endpoint")
```

- [ ] **Step 5: Implement the MillerPay client and provider adapters**

Create `walletquota/providers/millerpay/client.py`:

```python
import time
from typing import Callable, Optional

from walletquota.providers.signed_pool.client import (
    APIError,
    AuthenticationError,
    HTTPStatusError,
    SignedPoolClient,
    Transport,
    TransportError,
    UrllibTransport,
    make_signature,
)

from .config import MillerPaySettings
from .spec import MILLERPAY_SPEC


class MillerPayClient(SignedPoolClient):
    def __init__(
        self,
        settings: MillerPaySettings,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        super().__init__(
            settings,
            MILLERPAY_SPEC,
            transport=transport,
            sleep=sleep,
            clock_ms=clock_ms,
        )


__all__ = [
    "APIError",
    "AuthenticationError",
    "HTTPStatusError",
    "MillerPayClient",
    "Transport",
    "TransportError",
    "UrllibTransport",
    "make_signature",
]
```

Create `walletquota/providers/millerpay/provider.py`:

```python
from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)
from walletquota.providers.signed_pool.pool import collect_pool

from .client import MillerPayClient
from .config import ConfigError, MillerPaySettings
from .spec import MILLERPAY_SPEC


def _collect(settings: MillerPaySettings) -> CollectionResult:
    client = MillerPayClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    accounts = collect_accounts(client, MILLERPAY_SPEC)
    return snapshot, accounts


class MillerPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[MillerPaySettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=MILLERPAY_SPEC,
            settings_factory=lambda source, selected: MillerPaySettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="MillerPay configuration is invalid",
                authentication="MillerPay authentication failed",
                api="MillerPay API request failed",
                data="MillerPay returned invalid order data",
                incomplete="MillerPay order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
```

Create `walletquota/providers/millerpay/__init__.py`:

```python
"""MillerPay read-only order-pool provider."""

from .provider import MillerPayProvider

__all__ = ["MillerPayProvider"]
```

- [ ] **Step 6: Register MillerPay explicitly**

Modify `walletquota/registry.py` imports and factory map:

```python
from .providers.millerpay import MillerPayProvider


PROVIDER_FACTORIES: Mapping[str, ProviderFactory] = {
    "atgpay": lambda values, overrides: AtgPayProvider(values, overrides=overrides),
    "millerpay": lambda values, overrides: MillerPayProvider(values, overrides=overrides),
}
```

Do not add filesystem scanning, entry-point loading, dynamic imports, or provider aliases.

- [ ] **Step 7: Run MillerPay, registry, and ATG focused tests**

Run:

```bash
python3 -m unittest tests.test_miller_config tests.test_miller_provider tests.test_registry tests.test_atg_provider tests.test_client -v
```

Expected: all tests PASS. Confirm the default registry creates both providers in configured order without making network requests.

- [ ] **Step 8: Commit MillerPay provider registration**

```bash
git add walletquota/providers/millerpay walletquota/registry.py tests/test_miller_config.py tests/test_miller_provider.py tests/test_registry.py
git commit -m "feat: add millerpay order pool provider"
```

---

### Task 7: Configuration example, documentation, and cross-channel end-to-end proof

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `tests/test_end_to_end.py`
- Modify: `tests/test_wallet_quota_cli.py`
- Modify: `tests/test_scheduler_cli.py`
- Verify unchanged: `wallet_quota.py`
- Verify unchanged: `wallet_quota_scheduler.py`
- Verify unchanged: `walletquota/retention.py`

**Interfaces:**
- Consumes: registry key `millerpay`, existing CLI/reporting/scheduler entry points, and existing report privacy guarantees.
- Produces: documented local configuration and no-network proof that manual and scheduled execution preserve the same two-channel configuration and reporting semantics.

- [ ] **Step 1: Add manual and scheduler characterization tests**

In `tests/test_wallet_quota_cli.py`, add a verbose test with static `atgpay` and `millerpay` providers and assert both names appear, both are called once, and neither configured password appears in stdout/stderr.

In `tests/test_scheduler_cli.py`, change one wiring fixture to contain:

```dotenv
ENABLED_PROVIDERS=atgpay,millerpay
```

Keep the existing assertion that the scheduler passes the exact same `--env-file` to `wallet_quota.main`. Read the fixture before and after `wallet_quota_scheduler.main(...)` and assert the text is identical, proving the scheduler does not rewrite `ENABLED_PROVIDERS`.

Run:

```bash
python3 -m unittest tests.test_wallet_quota_cli tests.test_scheduler_cli -v
```

Expected: all tests PASS immediately as characterization coverage because the existing CLI and scheduler are already provider-neutral. If they fail, correct only the test fixture or expose an existing generic-wiring regression before continuing.

- [ ] **Step 2: Add a two-channel aggregate/reporting end-to-end test**

Extend `tests/test_end_to_end.py` with an in-memory ATG result and MillerPay result. Use MillerPay amounts on each exact boundary: `99.99`, `100`, `10000`, `20000`, and `50000.01`. Give MillerPay a complete UPI, bank number, and IFSC sentinel. Assert:

```python
self.assertIn("atgpay: success", terminal)
self.assertIn("millerpay: success", terminal)
self.assertNotIn(full_upi, terminal)
self.assertNotIn(full_account_number, terminal)
self.assertNotIn(full_ifsc, terminal)
self.assertEqual(set(bundle.channel_csv), {"atgpay", "millerpay"})
self.assertEqual(payload["channels"]["millerpay"]["accounts"][0]["upi"], full_upi)
self.assertNotIn(full_upi, bundle.channel_csv["millerpay"].read_text(encoding="utf-8"))
```

Recursively seed these forbidden keys in Miller metadata and order/account attributes: `password`, `loginToken`, `sessionKey`, `cookie`, `Signature`, and `authorization`. Assert none of their sentinel values appears in terminal, `aggregate.json`, or either CSV.

- [ ] **Step 3: Update `.env.example` without real credentials**

Set the example provider list and add MillerPay placeholders:

```dotenv
ENABLED_PROVIDERS=atgpay,millerpay

MILLERPAY_PHONE=
MILLERPAY_PASSWORD=
MILLERPAY_API_BASE=https://api.millerpay-app.com/app
MILLERPAY_SORT_TYPE=1
MILLERPAY_ORDER_TYPE=1
MILLERPAY_PAGE_SIZE=10
MILLERPAY_REQUEST_DELAY_MS=150
MILLERPAY_TIMEOUT_SECONDS=30
```

Keep the ATG section and shared `REPORT_DIRECTORY=reports`. Do not edit or stage the real `.env`.

- [ ] **Step 4: Update README for both providers and exact safety boundary**

Change “当前已接入 `atgpay`” to “当前已接入 `atgpay` 和 `millerpay`”. Show `ENABLED_PROVIDERS=atgpay,millerpay`, document the eight MillerPay configuration keys and default `orderType=1`, and show output containing both channel sections.

Document MillerPay’s exact six allowed POST paths separately from ATGPay’s six paths. State that both providers read the full pool and the same three account sources, and that empty accounts mean only that the current login returned no bound/claimed accounts.

Keep these explicit statements:

- scheduler runs at IST `:00`, `:20`, `:40` and requires a human restart;
- scheduler delegates to the same manual query and does not use Codex, cron, launchd, or startup services;
- only owned exported report snapshots expire at the IST date boundary;
- full account identifiers exist only in local `aggregate.json`;
- no receive-task, payment, UTR, bind, delete, or update endpoint is called.

- [ ] **Step 5: Run focused end-to-end and documentation checks**

Run:

```bash
python3 -m unittest tests.test_end_to_end tests.test_wallet_quota_cli tests.test_scheduler_cli tests.test_registry -v
rg -n "ENABLED_PROVIDERS=atgpay,millerpay|MILLERPAY_ORDER_TYPE=1|api.millerpay-app.com|app-web.millerpay-app.com" .env.example README.md
rg -n "/pay/receive/task|绝不会|不会.*付款|不会.*UTR" README.md docs/superpowers/specs/2026-08-18-millerpay-provider-design.md
```

Expected: all tests PASS; documentation searches show the confirmed defaults and explicit write-path prohibition. Inspect all matches to ensure `/pay/receive/task` appears only in prohibition text.

- [ ] **Step 6: Run the complete automated verification suite**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q walletquota atg_pool wallet_quota.py wallet_quota_scheduler.py atg_pool_report.py
git diff --check
git status --short
```

Expected: every test PASS with final `OK`; compileall and `git diff --check` produce no errors; status lists only the scoped Task 7 files before commit. No command should require or access the network.

- [ ] **Step 7: Commit documentation and cross-channel proof**

```bash
git add .env.example README.md tests/test_end_to_end.py tests/test_wallet_quota_cli.py tests/test_scheduler_cli.py
git commit -m "docs: enable atgpay and millerpay reporting"
```

- [ ] **Step 8: Perform the final read-only smoke verification**

Before running, verify locally that the ignored `.env` contains both provider names and MillerPay credential keys without printing their values:

```bash
python3 - <<'PY'
from pathlib import Path
from walletquota.config import read_env

values = read_env(Path(".env"))
required = {"MILLERPAY_PHONE", "MILLERPAY_PASSWORD"}
providers = {item.strip() for item in values.get("ENABLED_PROVIDERS", "").split(",")}
assert required.issubset(values) and all(values[key] for key in required)
assert {"atgpay", "millerpay"}.issubset(providers)
print("Local provider configuration is present")
PY
```

Then run one normal read-only query:

```bash
python3 wallet_quota.py --verbose
```

Expected: progress names only `atgpay, millerpay`; terminal contains `total:`, `atgpay:`, and `millerpay:`; a new owned report snapshot is written. Inspect `aggregate.json` structurally without printing account or auth values: both channels exist, amount-range counts/subtotals reconcile, and no recursively forbidden auth key exists. Do not run any unlisted endpoint and do not display the JSON file in terminal.

If local MillerPay credentials have not been placed in `.env`, stop after automated verification and report that the implementation is complete but the real smoke step is pending local configuration. Do not place credentials in source, shell history, command arguments, test fixtures, or the plan.

- [ ] **Step 9: Final repository verification**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: the tracked worktree is clean after the final commit. Ignored `.env` and generated reports do not appear. Confirm the implementation commits are on `main` and do not push unless the user separately requests it.
