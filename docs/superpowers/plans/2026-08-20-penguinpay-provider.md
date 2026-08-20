# PenguinPay Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `penguinpay` 作为第三个严格只读订单池渠道接入手动查询、20 分钟定时查询和综合报告。

**Architecture:** 新增一组 PenguinPay 薄适配器，仅定义受信任主机、配置和渠道身份。登录、MD5 签名、重试、稳定总数分页、三类账户采集和标准结果映射均复用 `walletquota.providers.signed_pool`；聚合、调度和保留实现不增加渠道分支。

**Tech Stack:** Python 3.9+ 标准库、`unittest`、`urllib`、现有 `walletquota` Provider/Runner/Reporting/Scheduler 架构。

## Global Constraints

- 保持 Python 3.9 兼容；新代码不使用 `X | None` 注解，不新增第三方依赖。
- PenguinPay API Base 固定为 `https://api.penguinpay-app.com/app`，Web Origin 固定为 `https://app-web.penguinpay-app.com`。
- 默认 `sortType=1`、`orderType=1`、`pageSize=10`、`requestDelayMs=150`、`timeoutSeconds=30`。
- 只允许 `/auth/login`、`/pay/debit/task`、`/user/upi/bind/list`、`/bank/debit/card/list`、`/pay/my/task`、`/pay/order/detail`。
- 不得调用 `/pay/receive/task`、`/pay/submit/utr`、`/pay/cancel/task`、`/user/account/wallet`、`/pay/debit/upis` 或任何其他未列出路径。
- 真实检查时的 49 单是动态现状，不得硬编码进实现或测试。
- 完整 UPI、卡号和 IFSC 只能进入本机 `aggregate.json`；终端必须掩码，渠道 CSV 必须排除账户字段。
- 密码、Token、Session、Cookie、Signature、Authorization、API Key、Credential 和原始认证响应不得进入日志或报告。
- 真实 `.env` 不得被自动化测试读取、改写、打印、暂存或提交。
- 不修改 `wallet_quota_scheduler.py`、`walletquota/scheduler.py`、`walletquota/retention.py` 的生产逻辑。
- 每个功能步骤都先观察预期 RED，再做最小 GREEN；每个 Task 独立提交并接受审查。

## File Structure

- Create `walletquota/providers/penguinpay/__init__.py`: 只导出 `PenguinPayProvider`。
- Create `walletquota/providers/penguinpay/spec.py`: PenguinPay 不可变 `ChannelSpec`。
- Create `walletquota/providers/penguinpay/config.py`: `PENGUINPAY_*` 配置解析、范围验证和凭据脱敏。
- Create `walletquota/providers/penguinpay/client.py`: 向 `SignedPoolClient` 注入 PenguinPay 规格。
- Create `walletquota/providers/penguinpay/provider.py`: 组合共享订单池与账户采集，并输出标准结果。
- Modify `walletquota/providers/signed_pool/spec.py`: 显式加入 PenguinPay 受信任 API/Origin 主机对。
- Modify `walletquota/registry.py`: 显式注册 `penguinpay`。
- Create `tests/test_penguin_spec.py`: 规格、域名和白名单契约。
- Create `tests/test_penguin_config.py`: 配置默认值、数值边界、受信任基址和密码脱敏。
- Create `tests/test_penguin_client.py`: 薄客户端的 Origin、URL、登录和签名身份。
- Create `tests/test_penguin_provider.py`: Provider 身份、默认值、采集顺序、错误脱敏和部分结果。
- Modify `tests/test_registry.py`: 三渠道默认注册和配置顺序。
- Modify `tests/test_end_to_end.py`: 三渠道报告、金额区间、账户隐私和认证脱敏。
- Modify `tests/test_wallet_quota_cli.py`: 三渠道手动查询和密码不输出。
- Modify `tests/test_scheduler_cli.py`: 三渠道 `.env` 原样透传。
- Create `tests/test_operator_examples.py`: `.env.example` 与 README 的 PenguinPay 运营契约。
- Modify `.env.example`: 加入 8 个 PenguinPay 占位配置。
- Modify `README.md`: 三渠道使用、输出、账户、安全和六路径文档。

---

### Task 1: Bind the PenguinPay identity to the exact read-only contract

**Files:**
- Create: `walletquota/providers/penguinpay/__init__.py`
- Create: `walletquota/providers/penguinpay/spec.py`
- Modify: `walletquota/providers/signed_pool/spec.py:18-22`
- Create: `tests/test_penguin_spec.py`

**Interfaces:**
- Consumes: `ChannelSpec` and `READ_ONLY_PATHS` from `walletquota.providers.signed_pool.spec`.
- Produces: `PENGUINPAY_SPEC: ChannelSpec` with provider name `penguinpay`, canonical API/Origin hosts, defaults `1/1`, and the exact six-path allowlist.

- [ ] **Step 1: Write the failing PenguinPay contract test**

Create `tests/test_penguin_spec.py`:

```python
from dataclasses import replace
from unittest import TestCase

from walletquota.providers.penguinpay.spec import PENGUINPAY_SPEC


class PenguinPaySpecTests(TestCase):
    def test_identity_defaults_and_exact_read_only_paths(self):
        self.assertEqual(PENGUINPAY_SPEC.name, "penguinpay")
        self.assertEqual(PENGUINPAY_SPEC.display_name, "PenguinPay")
        self.assertEqual(PENGUINPAY_SPEC.api_label, "PenguinPay")
        self.assertEqual(
            PENGUINPAY_SPEC.api_base, "https://api.penguinpay-app.com/app"
        )
        self.assertEqual(
            PENGUINPAY_SPEC.web_origin,
            "https://app-web.penguinpay-app.com",
        )
        self.assertEqual(
            (PENGUINPAY_SPEC.default_sort_type, PENGUINPAY_SPEC.default_order_type),
            (1, 1),
        )
        self.assertEqual(
            PENGUINPAY_SPEC.allowed_paths,
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

    def test_resolves_only_the_trusted_host_and_default_https_port(self):
        self.assertEqual(
            PENGUINPAY_SPEC.url_for(
                "https://api.penguinpay-app.com:443/app/", "/pay/debit/task"
            ),
            "https://api.penguinpay-app.com/app/pay/debit/task",
        )
        for value in (
            "http://api.penguinpay-app.com/app",
            "https://evil.example/app",
            "https://api.penguinpay-app.com:8443/app",
            "https://user:secret@api.penguinpay-app.com/app",
            "https://api.penguinpay-app.com/app/extra",
            "https://api.penguinpay-app.com/app?next=/app",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "trusted API endpoint"
            ):
                PENGUINPAY_SPEC.url_for(value, "/auth/login")

    def test_rejects_write_paths_and_spoofed_origin(self):
        for path in (
            "/pay/receive/task",
            "/pay/submit/utr",
            "/pay/cancel/task",
            "/user/account/wallet",
            "/pay/debit/upis",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "Unsupported API endpoint"
            ):
                PENGUINPAY_SPEC.url_for(PENGUINPAY_SPEC.api_base, path)
        with self.assertRaisesRegex(ValueError, "trusted web origin"):
            replace(PENGUINPAY_SPEC, web_origin="https://evil.example")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_penguin_spec -v
```

Expected: import fails because `walletquota.providers.penguinpay` does not exist.

- [ ] **Step 3: Add the trusted host pair and immutable spec**

Modify `_TRUSTED_PROVIDER_HOSTS` in `walletquota/providers/signed_pool/spec.py` to exactly:

```python
_TRUSTED_PROVIDER_HOSTS = {
    "atgpay": ("api.atg-game.com", "app-web.atg-game.com"),
    "millerpay": ("api.millerpay-app.com", "app-web.millerpay-app.com"),
    "penguinpay": ("api.penguinpay-app.com", "app-web.penguinpay-app.com"),
}
```

Create `walletquota/providers/penguinpay/__init__.py` with a package marker that does not import the not-yet-created Provider:

```python
"""PenguinPay read-only order-pool provider package."""
```

Create `walletquota/providers/penguinpay/spec.py`:

```python
from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


PENGUINPAY_SPEC = ChannelSpec(
    name="penguinpay",
    display_name="PenguinPay",
    api_label="PenguinPay",
    api_base="https://api.penguinpay-app.com/app",
    web_origin="https://app-web.penguinpay-app.com",
    user_agent="WalletQuota/penguinpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)
```

- [ ] **Step 4: Run focused and shared spec tests**

Run:

```bash
python3 -m unittest tests.test_penguin_spec tests.test_signed_pool_spec -v
```

Expected: all tests pass; existing ATGPay/MillerPay trusted-host behavior remains green.

- [ ] **Step 5: Commit Task 1**

```bash
git add walletquota/providers/signed_pool/spec.py walletquota/providers/penguinpay/__init__.py walletquota/providers/penguinpay/spec.py tests/test_penguin_spec.py
git commit -m "feat: define penguinpay read-only contract"
```

### Task 2: Add secret-safe configuration and the thin signed client

**Files:**
- Create: `walletquota/providers/penguinpay/config.py`
- Create: `walletquota/providers/penguinpay/client.py`
- Create: `tests/test_penguin_config.py`
- Create: `tests/test_penguin_client.py`

**Interfaces:**
- Consumes: `PENGUINPAY_SPEC` from Task 1 and `SignedPoolClient(settings, spec, transport, sleep, clock_ms)`.
- Produces: `PenguinPaySettings.from_values(values, overrides=None) -> PenguinPaySettings` and `PenguinPayClient(SignedPoolClient)`.

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_penguin_config.py`:

```python
from pathlib import Path
from unittest import TestCase

from walletquota.providers.penguinpay.config import ConfigError, PenguinPaySettings


class PenguinPaySettingsTests(TestCase):
    def test_defaults_match_the_confirmed_deposit_contract(self):
        value = PenguinPaySettings.from_values(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"}
        )

        self.assertEqual(value.api_base, "https://api.penguinpay-app.com/app")
        self.assertEqual((value.sort_type, value.order_type, value.page_size), (1, 1, 10))
        self.assertEqual(value.request_delay_ms, 150)
        self.assertEqual(value.timeout_seconds, 30.0)
        self.assertEqual(value.report_directory, Path("reports"))
        self.assertNotIn("secret", repr(value))

    def test_requires_both_credentials(self):
        cases = ({}, {"PENGUINPAY_PHONE": "phone"}, {"PENGUINPAY_PASSWORD": "secret"})
        for values in cases:
            with self.subTest(keys=tuple(values)), self.assertRaises(ConfigError):
                PenguinPaySettings.from_values(values)

    def test_accepts_only_supported_non_secret_values_and_overrides(self):
        value = PenguinPaySettings.from_values(
            {
                "PENGUINPAY_PHONE": "phone",
                "PENGUINPAY_PASSWORD": "secret",
                "PENGUINPAY_API_BASE": "https://api.penguinpay-app.com:443/app/",
                "PENGUINPAY_SORT_TYPE": "2",
                "PENGUINPAY_ORDER_TYPE": "3",
                "PENGUINPAY_PAGE_SIZE": "25",
                "PENGUINPAY_REQUEST_DELAY_MS": "0",
                "PENGUINPAY_TIMEOUT_SECONDS": "12.5",
                "REPORT_DIRECTORY": "penguin-reports",
            },
            overrides={
                "phone": "attacker-phone",
                "password": "attacker-password",
                "api_base": "https://evil.example/app",
                "sort_type": 7,
                "order_type": 8,
                "page_size": 50,
                "report_directory": Path("safe-reports"),
                "request_delay_ms": 1,
                "timeout_seconds": 1,
            },
        )

        self.assertEqual(value.phone, "phone")
        self.assertEqual(value.password, "secret")
        self.assertEqual(value.api_base, "https://api.penguinpay-app.com/app")
        self.assertEqual((value.sort_type, value.order_type, value.page_size), (7, 8, 50))
        self.assertEqual(value.report_directory, Path("safe-reports"))
        self.assertEqual(value.request_delay_ms, 0)
        self.assertEqual(value.timeout_seconds, 12.5)

    def test_rejects_invalid_numeric_and_untrusted_configuration(self):
        cases = (
            ("PENGUINPAY_SORT_TYPE", "many", "must be an integer"),
            ("PENGUINPAY_ORDER_TYPE", "many", "must be an integer"),
            ("PENGUINPAY_PAGE_SIZE", "0", "between 1 and 1000"),
            ("PENGUINPAY_PAGE_SIZE", "1001", "between 1 and 1000"),
            ("PENGUINPAY_REQUEST_DELAY_MS", "-1", "cannot be negative"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "0", "greater than zero"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "nan", "greater than zero"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "inf", "greater than zero"),
            ("PENGUINPAY_TIMEOUT_SECONDS", "soon", "must be a number"),
        )
        for key, raw, message in cases:
            values = {
                "PENGUINPAY_PHONE": "phone",
                "PENGUINPAY_PASSWORD": "secret",
                key: raw,
            }
            with self.subTest(key=key, raw=raw), self.assertRaisesRegex(
                ConfigError, message
            ):
                PenguinPaySettings.from_values(values)

        rejected = "https://evil.example/app?credential=raw-value"
        with self.assertRaisesRegex(ConfigError, "trusted PenguinPay endpoint") as raised:
            PenguinPaySettings.from_values(
                {
                    "PENGUINPAY_PHONE": "phone",
                    "PENGUINPAY_PASSWORD": "secret",
                    "PENGUINPAY_API_BASE": rejected,
                }
            )
        self.assertNotIn(rejected, str(raised.exception))
```

- [ ] **Step 2: Write the failing thin-client identity test**

Create `tests/test_penguin_client.py`:

```python
from unittest import TestCase

from walletquota.providers.penguinpay.client import PenguinPayClient, make_signature
from walletquota.providers.penguinpay.config import PenguinPaySettings


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, payload, headers, timeout):
        self.calls.append((url, dict(payload), dict(headers), timeout))
        return self.responses.pop(0)


class PenguinPayClientTests(TestCase):
    def test_uses_penguin_urls_origin_and_shared_signature(self):
        settings = PenguinPaySettings.from_values(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"}
        )
        transport = ScriptedTransport(
            [
                {
                    "code": 200,
                    "data": {
                        "userId": 50095029,
                        "loginToken": "login-token",
                        "sessionKey": "session-key",
                    },
                },
                {
                    "code": 200,
                    "data": {
                        "total": 0,
                        "list": [],
                        "inrBuyConfig": "",
                        "inrTodayBuyCount": 0,
                    },
                },
            ]
        )
        client = PenguinPayClient(
            settings,
            transport=transport,
            sleep=lambda delay: None,
            clock_ms=lambda: 100,
        )

        client.login()
        result = client.fetch_pool_page(1, 10, 1, 1)

        self.assertEqual(result["total"], 0)
        login_url, login_payload, login_headers, timeout = transport.calls[0]
        self.assertEqual(login_url, "https://api.penguinpay-app.com/app/auth/login")
        self.assertEqual(login_payload, {"phone": "phone", "password": "secret"})
        self.assertEqual(login_headers["Origin"], "https://app-web.penguinpay-app.com")
        self.assertNotIn("Signature", login_headers)
        self.assertEqual(timeout, 30.0)

        pool_url, pool_payload, pool_headers, unused_timeout = transport.calls[1]
        self.assertEqual(pool_url, "https://api.penguinpay-app.com/app/pay/debit/task")
        self.assertEqual(
            pool_payload,
            {
                "page": 1,
                "size": 10,
                "sortType": 1,
                "orderType": 1,
                "ts": 100,
                "userId": 50095029,
            },
        )
        self.assertEqual(
            pool_headers["Signature"], make_signature("session-key", pool_payload)
        )
```

- [ ] **Step 3: Run both test modules and verify RED**

Run:

```bash
python3 -m unittest tests.test_penguin_config tests.test_penguin_client -v
```

Expected: imports fail because `config.py` and `client.py` do not exist.

- [ ] **Step 4: Implement `PenguinPaySettings`**

Create `walletquota/providers/penguinpay/config.py`:

```python
from dataclasses import dataclass, field, replace
import math
from pathlib import Path
from typing import Any, Mapping, Optional

from .spec import PENGUINPAY_SPEC


class ConfigError(ValueError):
    """Raised when PenguinPay configuration is missing or invalid."""


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
        return PENGUINPAY_SPEC.normalized_api_base(value)
    except ValueError as error:
        raise ConfigError(
            "PENGUINPAY_API_BASE must be the trusted PenguinPay endpoint"
        ) from error


@dataclass(frozen=True)
class PenguinPaySettings:
    phone: str
    password: str = field(repr=False)
    api_base: str = PENGUINPAY_SPEC.api_base
    sort_type: int = PENGUINPAY_SPEC.default_sort_type
    order_type: int = PENGUINPAY_SPEC.default_order_type
    page_size: int = 10
    request_delay_ms: int = 150
    timeout_seconds: float = 30.0
    report_directory: Path = Path("reports")

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "PenguinPaySettings":
        for key in ("PENGUINPAY_PHONE", "PENGUINPAY_PASSWORD"):
            if not values.get(key):
                raise ConfigError("{} is required in configuration".format(key))
        settings = cls(
            phone=values["PENGUINPAY_PHONE"],
            password=values["PENGUINPAY_PASSWORD"],
            api_base=_trusted_api_base(
                values.get("PENGUINPAY_API_BASE", PENGUINPAY_SPEC.api_base)
            ),
            sort_type=_integer(values, "PENGUINPAY_SORT_TYPE", 1),
            order_type=_integer(values, "PENGUINPAY_ORDER_TYPE", 1),
            page_size=_integer(values, "PENGUINPAY_PAGE_SIZE", 10),
            request_delay_ms=_integer(
                values, "PENGUINPAY_REQUEST_DELAY_MS", 150
            ),
            timeout_seconds=_number(values, "PENGUINPAY_TIMEOUT_SECONDS", 30.0),
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
            raise ConfigError("PENGUINPAY_PAGE_SIZE must be between 1 and 1000")
        if self.request_delay_ms < 0:
            raise ConfigError("PENGUINPAY_REQUEST_DELAY_MS cannot be negative")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ConfigError("PENGUINPAY_TIMEOUT_SECONDS must be greater than zero")
        _trusted_api_base(self.api_base)
```

- [ ] **Step 5: Implement the thin client**

Create `walletquota/providers/penguinpay/client.py`:

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

from .config import PenguinPaySettings
from .spec import PENGUINPAY_SPEC


class PenguinPayClient(SignedPoolClient):
    def __init__(
        self,
        settings: PenguinPaySettings,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        super().__init__(
            settings,
            PENGUINPAY_SPEC,
            transport=transport,
            sleep=sleep,
            clock_ms=clock_ms,
        )


__all__ = [
    "APIError",
    "AuthenticationError",
    "HTTPStatusError",
    "PenguinPayClient",
    "Transport",
    "TransportError",
    "UrllibTransport",
    "make_signature",
]
```

- [ ] **Step 6: Run focused and shared client/config regressions**

Run:

```bash
python3 -m unittest tests.test_penguin_config tests.test_penguin_client tests.test_miller_config tests.test_signed_pool_client tests.test_config tests.test_client -v
```

Expected: all tests pass; ATGPay and MillerPay settings/signature behavior remains green.

- [ ] **Step 7: Commit Task 2**

```bash
git add walletquota/providers/penguinpay/config.py walletquota/providers/penguinpay/client.py tests/test_penguin_config.py tests/test_penguin_client.py
git commit -m "feat: add penguinpay signed client configuration"
```

### Task 3: Map PenguinPay results and register the provider

**Files:**
- Create: `walletquota/providers/penguinpay/provider.py`
- Modify: `walletquota/providers/penguinpay/__init__.py`
- Modify: `walletquota/registry.py:1-19`
- Create: `tests/test_penguin_provider.py`
- Modify: `tests/test_registry.py:3-45`

**Interfaces:**
- Consumes: `PenguinPaySettings`, `PenguinPayClient`, `PENGUINPAY_SPEC`, `collect_pool`, `collect_accounts`, and `SignedPoolProvider`.
- Produces: `PenguinPayProvider(values, overrides=None, collect=_collect)` with `name == "penguinpay"`; `PROVIDER_FACTORIES["penguinpay"]`.

- [ ] **Step 1: Write the failing Provider tests**

Create `tests/test_penguin_provider.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch

from walletquota.models import Account
from walletquota.providers.penguinpay.config import PenguinPaySettings
from walletquota.providers.penguinpay.provider import PenguinPayProvider, _collect
from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.client import APIError, AuthenticationError
from walletquota.providers.signed_pool.pool import Order, PoolDataError, PoolSnapshot


def complete_snapshot(complete=True):
    now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    return PoolSnapshot(
        orders=(
            Order(
                order_no="PP-1",
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
            Account("penguinpay", "bound_upi", "upi-1", upi="local-test@upi"),
        ),
        warnings=(),
        bound_upi_total=1,
        bank_card_total=0,
        claimed_task_total=0,
    )


class PenguinPayProviderTests(TestCase):
    def test_maps_complete_snapshot_with_penguin_identity_and_defaults(self):
        captured = []
        provider = PenguinPayProvider(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"},
            collect=lambda settings: captured.append(settings)
            or (complete_snapshot(), account_snapshot()),
        )

        result = provider.run()

        self.assertEqual(
            (provider.name, result.provider, result.status),
            ("penguinpay", "penguinpay", "success"),
        )
        self.assertEqual(
            (captured[0].sort_type, captured[0].order_type, captured[0].page_size),
            (1, 1, 10),
        )
        self.assertEqual(result.orders[0].provider, "penguinpay")
        self.assertEqual(result.accounts[0].provider, "penguinpay")

    def test_incomplete_snapshot_is_partial_and_errors_are_redacted(self):
        incomplete = PenguinPayProvider(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"},
            collect=lambda settings: complete_snapshot(False),
        ).run()
        self.assertEqual(incomplete.status, "partial")

        cases = (
            (AuthenticationError("raw-auth"), "authentication"),
            (APIError("raw-api"), "api"),
            (PoolDataError("raw-data"), "data"),
        )
        for error, category in cases:
            provider = PenguinPayProvider(
                {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"},
                collect=lambda settings, raised=error: (_ for _ in ()).throw(raised),
            )
            result = provider.run()
            self.assertEqual(result.error.category, category)
            self.assertNotIn("raw-", result.error.message)

    def test_missing_credentials_is_a_channel_configuration_error(self):
        result = PenguinPayProvider(
            {}, collect=lambda settings: complete_snapshot()
        ).run()
        self.assertEqual(result.error.category, "configuration")

    def test_collect_logs_in_then_reads_pool_and_all_account_sources(self):
        settings = PenguinPaySettings.from_values(
            {"PENGUINPAY_PHONE": "phone", "PENGUINPAY_PASSWORD": "secret"}
        )
        events = []
        client = Mock()
        with patch(
            "walletquota.providers.penguinpay.provider.PenguinPayClient",
            return_value=client,
        ) as client_factory, patch(
            "walletquota.providers.penguinpay.provider.collect_pool",
            side_effect=lambda selected_client, selected_settings: events.append("pool")
            or complete_snapshot(),
        ) as pool_collector, patch(
            "walletquota.providers.penguinpay.provider.collect_accounts",
            side_effect=lambda selected_client, spec: events.append("accounts")
            or account_snapshot(),
        ) as account_collector:
            client.login.side_effect = lambda: events.append("login")
            result = _collect(settings)

        self.assertEqual(events, ["login", "pool", "accounts"])
        self.assertEqual(result, (complete_snapshot(), account_snapshot()))
        client_factory.assert_called_once_with(settings)
        pool_collector.assert_called_once_with(client, settings)
        account_collector.assert_called_once()
```

- [ ] **Step 2: Extend the default registry test and verify RED**

In `tests/test_registry.py`, import `PenguinPayProvider` and replace the first registry test with:

```python
def test_default_registry_builds_three_channels_in_configured_order(self):
    settings = AppSettings.from_values(
        {
            "ENABLED_PROVIDERS": "penguinpay,millerpay,atgpay",
            "PENGUINPAY_PHONE": "penguin-phone",
            "PENGUINPAY_PASSWORD": "penguin-secret",
            "MILLERPAY_PHONE": "miller-phone",
            "MILLERPAY_PASSWORD": "miller-secret",
            "ATGPAY_PHONE": "atg-phone",
            "ATGPAY_PASSWORD": "atg-secret",
        }
    )

    providers = build_providers(settings)

    self.assertEqual(
        [provider.name for provider in providers],
        ["penguinpay", "millerpay", "atgpay"],
    )
    self.assertIsInstance(providers[0], PenguinPayProvider)
    self.assertIsInstance(providers[1], MillerPayProvider)
```

Run:

```bash
python3 -m unittest tests.test_penguin_provider tests.test_registry -v
```

Expected: Provider import/registry tests fail because `provider.py` and the factory do not exist.

- [ ] **Step 3: Implement the PenguinPay Provider**

Create `walletquota/providers/penguinpay/provider.py`:

```python
from typing import Any, Callable, Mapping, Optional

from walletquota.providers.signed_pool.accounts import collect_accounts
from walletquota.providers.signed_pool.provider import (
    CollectionResult,
    ProviderMessages,
    SignedPoolProvider,
)
from walletquota.providers.signed_pool.pool import collect_pool

from .client import PenguinPayClient
from .config import ConfigError, PenguinPaySettings
from .spec import PENGUINPAY_SPEC


def _collect(settings: PenguinPaySettings) -> CollectionResult:
    client = PenguinPayClient(settings)
    client.login()
    snapshot = collect_pool(client, settings)
    accounts = collect_accounts(client, PENGUINPAY_SPEC)
    return snapshot, accounts


class PenguinPayProvider(SignedPoolProvider):
    def __init__(
        self,
        values: Mapping[str, str],
        overrides: Optional[Mapping[str, Any]] = None,
        collect: Callable[[PenguinPaySettings], CollectionResult] = _collect,
    ) -> None:
        super().__init__(
            values=values,
            spec=PENGUINPAY_SPEC,
            settings_factory=lambda source, selected: PenguinPaySettings.from_values(
                source, overrides=selected
            ),
            config_error=ConfigError,
            messages=ProviderMessages(
                configuration="PenguinPay configuration is invalid",
                authentication="PenguinPay authentication failed",
                api="PenguinPay API request failed",
                data="PenguinPay returned invalid order data",
                incomplete="PenguinPay order-pool snapshot is incomplete",
            ),
            collect=collect,
            overrides=overrides,
        )
```

Replace `walletquota/providers/penguinpay/__init__.py` with:

```python
"""PenguinPay read-only order-pool provider."""

from .provider import PenguinPayProvider

__all__ = ["PenguinPayProvider"]
```

- [ ] **Step 4: Register the provider explicitly**

Add the import in `walletquota/registry.py`:

```python
from .providers.penguinpay import PenguinPayProvider
```

Add the factory immediately after `millerpay`:

```python
"penguinpay": lambda values, overrides: PenguinPayProvider(
    values, overrides=overrides
),
```

- [ ] **Step 5: Run focused Provider, registry, pool and account regressions**

Run:

```bash
python3 -m unittest tests.test_penguin_provider tests.test_registry tests.test_signed_pool_provider tests.test_signed_pool_pool tests.test_signed_pool_accounts tests.test_miller_provider tests.test_atg_provider -v
```

Expected: all tests pass; PenguinPay uses shared collection and provider-specific identity without altering ATGPay/MillerPay.

- [ ] **Step 6: Commit Task 3**

```bash
git add walletquota/providers/penguinpay/__init__.py walletquota/providers/penguinpay/provider.py walletquota/registry.py tests/test_penguin_provider.py tests/test_registry.py
git commit -m "feat: register penguinpay order pool provider"
```

### Task 4: Expose three-channel configuration, reporting and operator documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Create: `tests/test_operator_examples.py`
- Modify: `tests/test_end_to_end.py`
- Modify: `tests/test_wallet_quota_cli.py`
- Modify: `tests/test_scheduler_cli.py`

**Interfaces:**
- Consumes: registered `penguinpay` Provider and unchanged aggregate/report/scheduler interfaces.
- Produces: three-channel examples, terminal/JSON/CSV privacy proof, and operator instructions for manual and scheduled queries.

- [ ] **Step 1: Add a failing operator-example contract test**

Create `tests/test_operator_examples.py`:

```python
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class OperatorExampleTests(TestCase):
    def test_penguinpay_configuration_and_read_only_contract_are_documented(self):
        env_lines = set(
            (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        expected_env_lines = {
            "ENABLED_PROVIDERS=atgpay,millerpay,penguinpay",
            "PENGUINPAY_PHONE=",
            "PENGUINPAY_PASSWORD=",
            "PENGUINPAY_API_BASE=https://api.penguinpay-app.com/app",
            "PENGUINPAY_SORT_TYPE=1",
            "PENGUINPAY_ORDER_TYPE=1",
            "PENGUINPAY_PAGE_SIZE=10",
            "PENGUINPAY_REQUEST_DELAY_MS=150",
            "PENGUINPAY_TIMEOUT_SECONDS=30",
        }
        self.assertTrue(expected_env_lines.issubset(env_lines))

        for fact in (
            "penguinpay:",
            "https://api.penguinpay-app.com/app",
            "https://app-web.penguinpay-app.com",
            "sortType=1",
            "orderType=1",
            "pageSize=10",
            "penguinpay_orders.csv",
            "channels.penguinpay.accounts",
            "账户: 0",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact, readme)

        for path in (
            "/auth/login",
            "/pay/debit/task",
            "/user/upi/bind/list",
            "/bank/debit/card/list",
            "/pay/my/task",
            "/pay/order/detail",
        ):
            with self.subTest(path=path):
                self.assertIn("POST {}".format(path), readme)

        self.assertIn("永不调用", readme)
        for forbidden in (
            "/pay/receive/task",
            "/pay/submit/utr",
            "/pay/cancel/task",
            "/user/account/wallet",
            "/pay/debit/upis",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, readme)
```

Run:

```bash
python3 -m unittest tests.test_operator_examples -v
```

Expected: RED because `.env.example` and README do not yet contain the PenguinPay operator contract.

- [ ] **Step 2: Add the three-channel report privacy regression**

Add this method to `ComprehensiveOutputTests` in `tests/test_end_to_end.py`:

```python
def test_three_channels_report_ranges_accounts_and_secrets_safely(self):
    secret = "penguin-session-secret-sentinel"
    penguin_orders = tuple(
        Order(
            "penguinpay",
            "P{}".format(index),
            amount,
            attributes={"sessionKey": secret, "safe": "kept"},
        )
        for index, amount in enumerate(
            (
                Decimal("99.99"),
                Decimal("100"),
                Decimal("10000"),
                Decimal("20000"),
                Decimal("50000.01"),
            ),
            start=1,
        )
    )
    full_upi = "penguin.collector@upi"
    full_account = "987654321012"
    full_ifsc = "HDFC0000456"
    channels = (
        ProviderResult.success(
            "atgpay", (Order("atgpay", "A1", Decimal("1")),), {}
        ),
        ProviderResult.success(
            "millerpay", (Order("millerpay", "M1", Decimal("2")),), {}
        ),
        ProviderResult.success(
            "penguinpay",
            penguin_orders,
            {"credential": secret, "safe": "kept"},
            (
                Account(
                    "penguinpay",
                    "bound_upi",
                    "penguin-upi",
                    upi=full_upi,
                    attributes={"authorization": secret},
                ),
                Account(
                    "penguinpay",
                    "bank_account",
                    "penguin-bank",
                    account=full_account,
                    bank_name="HDFC",
                    ifsc=full_ifsc,
                    attributes={"apiKey": secret},
                ),
            ),
        ),
    )
    now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    aggregate = aggregate_results(channels, now, now)
    terminal = format_terminal_report(aggregate)

    self.assertLess(terminal.index("atgpay:"), terminal.index("millerpay:"))
    self.assertLess(terminal.index("millerpay:"), terminal.index("penguinpay:"))
    self.assertEqual(
        [
            (row["label"], row["count"])
            for row in summarize_orders(penguin_orders)["amount_distribution"]
        ],
        [
            ("<100", 1),
            ("100-10000", 1),
            ("10000-20000", 1),
            ("20000-50000", 1),
            (">50000", 1),
        ],
    )
    self.assertNotIn(full_upi, terminal)
    self.assertNotIn(full_account, terminal)
    self.assertNotIn(full_ifsc, terminal)
    self.assertNotIn(secret, terminal)

    with tempfile.TemporaryDirectory() as temporary:
        bundle = write_report_bundle(aggregate, Path(temporary))
        aggregate_text = bundle.aggregate_json.read_text(encoding="utf-8")
        payload = json.loads(aggregate_text)
        penguin_csv = bundle.channel_csv["penguinpay"].read_text(encoding="utf-8")

    self.assertEqual(
        set(bundle.channel_csv), {"atgpay", "millerpay", "penguinpay"}
    )
    accounts = payload["channels"]["penguinpay"]["accounts"]
    self.assertEqual(accounts[0]["upi"], full_upi)
    self.assertEqual(accounts[1]["account"], full_account)
    self.assertEqual(accounts[1]["ifsc"], full_ifsc)
    self.assertNotIn(full_upi, penguin_csv)
    self.assertNotIn(full_account, penguin_csv)
    self.assertNotIn(full_ifsc, penguin_csv)
    self.assertNotIn(secret, aggregate_text)
    self.assertNotIn(secret, penguin_csv)
```

- [ ] **Step 3: Extend manual and scheduled CLI regression coverage**

Replace `test_verbose_calls_atgpay_and_millerpay_without_printing_passwords` in `tests/test_wallet_quota_cli.py` with:

```python
def test_verbose_calls_three_channels_without_printing_passwords(self):
    self.env.write_text(
        "ENABLED_PROVIDERS=atgpay,millerpay,penguinpay\n"
        "ATGPAY_PASSWORD=atgpay-password\n"
        "MILLERPAY_PASSWORD=millerpay-password\n"
        "PENGUINPAY_PASSWORD=penguinpay-password\n",
        encoding="utf-8",
    )
    providers = (
        self.success_provider("atgpay"),
        self.success_provider("millerpay"),
        self.success_provider("penguinpay"),
    )

    code, output, errors = self.run_main(providers, ("--verbose",))

    self.assertEqual(code, 0)
    for provider in providers:
        self.assertEqual(provider.calls, 1)
        self.assertIn(provider.name, errors)
    for password in (
        "atgpay-password",
        "millerpay-password",
        "penguinpay-password",
    ):
        self.assertNotIn(password, output + errors)
```

Change the scheduler fixture in `tests/test_scheduler_cli.py` to:

```python
self.env_file.write_text(
    "ENABLED_PROVIDERS=atgpay,millerpay,penguinpay\n", encoding="utf-8"
)
```

Run:

```bash
python3 -m unittest tests.test_end_to_end.ComprehensiveOutputTests.test_three_channels_report_ranges_accounts_and_secrets_safely tests.test_wallet_quota_cli.WalletQuotaCliTests.test_verbose_calls_three_channels_without_printing_passwords tests.test_scheduler_cli.SchedulerCliTests.test_wires_resolved_query_arguments_and_cleanup -v
```

Expected: all three regression tests pass immediately against the provider-neutral report, manual CLI, and scheduler implementations. The failing operator-example test from Step 1 remains the Task 4 RED driver until the examples and documentation are updated.

- [ ] **Step 4: Add placeholders to `.env.example`**

Set the enabled list to:

```dotenv
ENABLED_PROVIDERS=atgpay,millerpay,penguinpay
```

Insert before `REPORT_DIRECTORY=reports`:

```dotenv
PENGUINPAY_PHONE=
PENGUINPAY_PASSWORD=
PENGUINPAY_API_BASE=https://api.penguinpay-app.com/app
PENGUINPAY_SORT_TYPE=1
PENGUINPAY_ORDER_TYPE=1
PENGUINPAY_PAGE_SIZE=10
PENGUINPAY_REQUEST_DELAY_MS=150
PENGUINPAY_TIMEOUT_SECONDS=30
```

- [ ] **Step 5: Document the exact operator contract**

Update `README.md` with all of these exact facts:

```text
penguinpay: PenguinPay 的订单数、金额、金额分布或错误状态
ENABLED_PROVIDERS=atgpay,millerpay,penguinpay
PenguinPay API Base: https://api.penguinpay-app.com/app
PenguinPay Web Origin: https://app-web.penguinpay-app.com
PenguinPay 默认 sortType=1、orderType=1、pageSize=10
PenguinPay 账户来源：绑定 UPI、绑定银行卡、已领取订单详情
`penguinpay_orders.csv` 不包含账户字段
`channels.penguinpay.accounts` 可包含完整本机账户标识
`账户: 0` 只表示当前登录未返回账户，不表示渠道没有账户
```

Add a separate PenguinPay allowlist block with exactly:

```text
POST /auth/login
POST /pay/debit/task
POST /user/upi/bind/list
POST /bank/debit/card/list
POST /pay/my/task
POST /pay/order/detail
```

State explicitly that PenguinPay never calls `/pay/receive/task`, `/pay/submit/utr`, `/pay/cancel/task`, `/user/account/wallet`, `/pay/debit/upis`, payment, bind, unbind, delete, update, or other mutation endpoints. Keep the existing human-restart scheduler, IST `:00/:20/:40`, no Codex/cron/launchd/startup, and owned-export-only retention wording unchanged.

- [ ] **Step 6: Run all Task 4 focused tests and documentation checks**

Run:

```bash
python3 -m unittest tests.test_operator_examples tests.test_end_to_end tests.test_wallet_quota_cli tests.test_scheduler_cli tests.test_registry -v
rg -n "ENABLED_PROVIDERS=atgpay,millerpay,penguinpay|PENGUINPAY_ORDER_TYPE=1|api.penguinpay-app.com/app|app-web.penguinpay-app.com|/pay/receive/task|/pay/submit/utr|/pay/cancel/task" .env.example README.md
git diff --check
```

Expected: focused tests pass; docs contain all required PenguinPay values and only prohibition text references mutation paths.

- [ ] **Step 7: Run complete local verification**

Run:

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/walletquota-penguinpay-pycache python3 -m compileall -q walletquota atg_pool wallet_quota.py wallet_quota_scheduler.py
git diff --check
git status --short
```

Expected: all tests and compilation pass; the status contains only Task 4 files before commit and never includes `.env` or report snapshots.

- [ ] **Step 8: Commit Task 4**

```bash
git add .env.example README.md tests/test_operator_examples.py tests/test_end_to_end.py tests/test_wallet_quota_cli.py tests/test_scheduler_cli.py
git commit -m "docs: expose penguinpay three-channel workflow"
```

- [ ] **Step 9: Perform controller-owned final read-only smoke verification**

After all automated tests and code reviews pass, the controller may instantiate `PenguinPayProvider` with the user-authorized credentials held only in memory and call `run()` once. The smoke verification must print only provider status, order count, aggregate amount, five range counts, account count, warning count, and whether pagination was complete; it must not print request headers, payloads, order numbers, account identifiers, authentication fields, raw responses, or credential values. It may call only the six allowlisted paths and must not create or modify `.env`.

Expected: `penguinpay` returns `success` when the server total remains stable, or `partial/error` with a fixed safe category when the live server changes; either outcome is reported without secrets or mutation requests.

## Final Review Gate

- Request a fresh read-only review of the complete range from the design-plan base through Task 4.
- Resolve every Critical and Important finding with a reproducing test before completion.
- Re-run the full test suite, compile check, `git diff --check`, and clean-status check after the final fix.
- Confirm the runtime source contains only the exact six PenguinPay paths and no call site for `/pay/receive/task`, `/pay/submit/utr`, `/pay/cancel/task`, `/user/account/wallet`, or `/pay/debit/upis`.
