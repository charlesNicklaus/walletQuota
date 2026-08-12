# Multi-Provider Order-Pool Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the ATG-only reporter into a concurrent, failure-isolated provider framework that emits one trustworthy `total` plus named channel results, with ATG Pay as the first provider.

**Architecture:** A provider registry builds explicitly trusted providers from `.env`; a bounded concurrent runner converts every provider execution into a standard result; the aggregate and reporting layers operate only on provider-neutral models. ATG protocol code moves under `walletquota/providers/atgpay/`, while `atg_pool` and `atg_pool_report.py` remain compatibility surfaces.

**Tech Stack:** Python 3.9+ standard library, `dataclasses`, `decimal.Decimal`, `concurrent.futures.ThreadPoolExecutor`, `urllib`, `unittest`.

## Global Constraints

- Python 3.9 or newer; no third-party runtime dependencies.
- All currencies are `INR`; never combine a result whose currency differs.
- Run every enabled provider concurrently with a fixed maximum of eight workers.
- A provider failure or incomplete snapshot must not prevent other providers or report generation.
- `total` includes only providers with `status == "success"` and complete pagination.
- Never log or report passwords, login tokens, session keys, cookies, signatures, or raw authentication responses.
- Providers may call only their explicitly allowlisted HTTPS hosts and read-only order-pool endpoints.
- Tests use local fakes and must never access a real payment API.
- Work directly on the user-approved `main` branch and preserve unrelated worktree changes.

---

### Task 1: Checkpoint the working ATG baseline

**Files:**
- Modify: `.gitignore`
- Commit unchanged baseline files: `.env.example`, `README.md`, `atg_pool/`, `atg_pool_report.py`, `tests/`, `docs/superpowers/specs/2026-08-12-atg-order-pool-report-design.md`, `docs/superpowers/plans/2026-08-12-atg-order-pool-report.md`

**Interfaces:**
- Consumes: the existing tested ATG-only implementation.
- Produces: a clean Git checkpoint from which the architecture migration can be reviewed.

- [ ] **Step 1: Ignore generated visual-companion state**

Add this exact line to `.gitignore`:

```gitignore
.superpowers/
```

- [ ] **Step 2: Verify the baseline before committing it**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/walletQuota-pyc python3 -m compileall -q atg_pool atg_pool_report.py tests
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: compilation exits `0`, all 39 baseline tests pass, and `git diff --check` prints nothing.

- [ ] **Step 3: Commit only the baseline implementation**

```bash
git add .gitignore .env.example README.md atg_pool atg_pool_report.py tests \
  docs/superpowers/specs/2026-08-12-atg-order-pool-report-design.md \
  docs/superpowers/plans/2026-08-12-atg-order-pool-report.md
git commit -m "feat: add ATG order pool report"
```

Expected: the generated `.superpowers/` directory remains ignored and is absent from the commit.

---

### Task 2: Add provider-neutral models and summary calculations

**Files:**
- Create: `walletquota/__init__.py`
- Create: `walletquota/models.py`
- Create: `walletquota/summary.py`
- Create: `tests/test_models.py`
- Create: `tests/test_summary.py`

**Interfaces:**
- Consumes: finite `Decimal` amounts and provider-returned order data.
- Produces: `Order`, `ProviderError`, `ProviderResult`, `AggregateResult`, `summarize_orders(orders)` and `empty_summary()`.

- [ ] **Step 1: Write failing model validation tests**

Create tests that instantiate these exact public types:

```python
from decimal import Decimal
from unittest import TestCase

from walletquota.models import Order, ProviderResult


class ModelTests(TestCase):
    def test_order_requires_inr_and_finite_amount(self):
        with self.assertRaises(ValueError):
            Order("atgpay", "A1", Decimal("NaN"))
        with self.assertRaises(ValueError):
            Order("atgpay", "A1", Decimal("10"), currency="USD")

    def test_success_result_must_be_complete(self):
        order = Order("atgpay", "A1", Decimal("10"))
        result = ProviderResult.success("atgpay", (order,), {"pages": 1})
        self.assertEqual(result.status, "success")
        self.assertTrue(result.complete)
```

- [ ] **Step 2: Run the model tests and confirm the import failure**

Run: `python3 -m unittest tests.test_models -v`

Expected: FAIL because `walletquota.models` does not exist.

- [ ] **Step 3: Implement immutable standard models**

Implement:

```python
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

    @classmethod
    def success(cls, provider, orders, metadata):
        return cls(provider, "success", tuple(orders), True, dict(metadata))

    @classmethod
    def partial(cls, provider, orders, metadata, message):
        return cls(provider, "partial", tuple(orders), False, dict(metadata),
                   ProviderError("incomplete", message))

    @classmethod
    def failure(cls, provider, category, message):
        return cls(provider, "error", (), False, {}, ProviderError(category, message))


@dataclass(frozen=True)
class AggregateResult:
    status: str
    channels: Tuple[ProviderResult, ...]
    total: Mapping[str, Any]
    started_at: datetime
    finished_at: datetime
```

Use `MappingProxyType(dict(attributes))` and `MappingProxyType(dict(metadata))` in `__post_init__` so callers cannot mutate frozen model contents.

- [ ] **Step 4: Write failing provider-neutral summary tests**

Cover exact amount buckets, mean, median, optional income coverage, empty input, and two providers using the same order ID:

```python
orders = (
    Order("atgpay", "A1", Decimal("10"), income=Decimal("1")),
    Order("xxxpay", "A1", Decimal("20")),
)
summary = summarize_orders(orders)
self.assertEqual(summary["order_count"], 2)
self.assertEqual(summary["order_amount"], Decimal("30"))
self.assertEqual(summary["income_coverage"], 1)
self.assertEqual([row["count"] for row in summary["amount_distribution"]], [1, 1])
```

- [ ] **Step 5: Implement `summarize_orders` using the existing Decimal rules**

Move the generic parts of `atg_pool.report.build_summary` into `walletquota.summary`; use keys `order_count`, `order_amount`, `minimum_amount`, `maximum_amount`, `mean_amount`, `median_amount`, `total_income`, `income_coverage`, `total_balance`, `balance_coverage`, and `amount_distribution`. Each distribution entry is a dictionary containing `amount`, `count`, `percentage`, and `subtotal`.

- [ ] **Step 6: Run the focused and full tests**

```bash
python3 -m unittest tests.test_models tests.test_summary -v
python3 -m unittest discover -s tests -v
```

Expected: all new tests and all 39 baseline tests pass.

- [ ] **Step 7: Commit the standard model layer**

```bash
git add walletquota/__init__.py walletquota/models.py walletquota/summary.py \
  tests/test_models.py tests/test_summary.py
git commit -m "feat: add provider-neutral order models"
```

---

### Task 3: Move ATG protocol code behind `AtgPayProvider`

**Files:**
- Create: `walletquota/providers/__init__.py`
- Create: `walletquota/providers/base.py`
- Create: `walletquota/providers/atgpay/__init__.py`
- Create: `walletquota/providers/atgpay/config.py`
- Create: `walletquota/providers/atgpay/client.py`
- Create: `walletquota/providers/atgpay/pool.py`
- Create: `walletquota/providers/atgpay/provider.py`
- Modify: `atg_pool/config.py`
- Modify: `atg_pool/client.py`
- Modify: `atg_pool/pool.py`
- Modify: `atg_pool/report.py`
- Create: `tests/test_atg_provider.py`

**Interfaces:**
- Consumes: a string environment mapping and optional non-secret overrides.
- Produces: `Provider` protocol and `AtgPayProvider(name="atgpay").run() -> ProviderResult`.

- [ ] **Step 1: Write failing Provider contract and ATG mapping tests**

Use a fake collector so no network is accessed:

```python
provider = AtgPayProvider(
    {"ATGPAY_PHONE": "9000000000", "ATGPAY_PASSWORD": "secret"},
    collect=lambda settings: existing_snapshot,
)
result = provider.run()
self.assertEqual(result.provider, "atgpay")
self.assertEqual(result.status, "success")
self.assertEqual(result.orders[0].provider, "atgpay")
self.assertEqual(result.orders[0].order_id, "AP1")
self.assertEqual(result.orders[0].currency, "INR")
```

Also assert that `ATGPAY_*` overrides legacy `ATG_*`, legacy-only values still work, partial snapshots become `partial`, missing credentials become category `configuration`, authentication errors become category `authentication`, and `repr(provider.settings)` excludes the password.

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `python3 -m unittest tests.test_atg_provider -v`

Expected: FAIL because the provider package does not exist.

- [ ] **Step 3: Define the Provider protocol**

```python
class Provider(Protocol):
    @property
    def name(self) -> str:
        ...

    def run(self) -> ProviderResult:
        ...
```

The two protocol method bodies use Python protocol ellipsis expressions; they are intentional executable stubs, not implementation placeholders.

- [ ] **Step 4: Relocate ATG settings, client, and pool collector**

Copy the tested implementations into `walletquota/providers/atgpay/`, then convert the old `atg_pool` files into imports that re-export their public names. Preserve these exact legacy imports:

```python
from walletquota.providers.atgpay.client import *
from walletquota.providers.atgpay.config import *
from walletquota.providers.atgpay.pool import *
```

Add `AtgSettings.from_values(values, overrides=None)`. For each setting, read `ATGPAY_*` first and its existing `ATG_*` counterpart second. Keep `Settings = AtgSettings` so old imports and tests remain valid.

- [ ] **Step 5: Implement `AtgPayProvider.run`**

Construct `AtgClient`, call `login`, call `collect_pool`, and map each ATG order to the standard model:

```python
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
```

Return `success` only when `snapshot.complete` is true. Catch `ConfigError`, `AuthenticationError`, other `APIError`, and `PoolDataError` separately and return safe categories `configuration`, `authentication`, `api`, and `data`. Do not include caught response bodies in messages.

- [ ] **Step 6: Keep the old ATG report adapter passing**

Update `atg_pool/report.py` imports to use the relocated ATG order and snapshot types. Do not change its existing public `build_summary`, `format_terminal_report`, or `write_reports` behavior in this task.

- [ ] **Step 7: Run ATG provider and full regression tests**

```bash
python3 -m unittest tests.test_atg_provider -v
python3 -m unittest discover -s tests -v
```

Expected: the provider tests and all baseline tests pass.

- [ ] **Step 8: Commit the ATG provider boundary**

```bash
git add walletquota/providers atg_pool tests/test_atg_provider.py
git commit -m "refactor: move ATG behind provider interface"
```

---

### Task 4: Add application configuration and explicit provider registry

**Files:**
- Create: `walletquota/config.py`
- Create: `walletquota/registry.py`
- Create: `tests/test_app_config.py`
- Create: `tests/test_registry.py`

**Interfaces:**
- Consumes: `.env` path, optional `enabled_providers`, output-directory override, and the trusted factory mapping.
- Produces: `AppSettings.load(path)`, `build_providers(settings, factories=PROVIDER_FACTORIES)` and `UnavailableProvider`.

- [ ] **Step 1: Write failing application configuration tests**

Cover:

```python
self.assertEqual(
    AppSettings.from_values({"ENABLED_PROVIDERS": "atgpay, xxxpay,atgpay"}).enabled_providers,
    ("atgpay", "xxxpay"),
)
self.assertEqual(AppSettings.from_values({}).enabled_providers, ("atgpay",))
```

Also assert an explicitly empty `ENABLED_PROVIDERS`, an unreadable env file, an explicitly empty `REPORT_DIRECTORY`, and an empty enabled-provider CLI override raise `ConfigError` without including secret values.

- [ ] **Step 2: Implement `.env` parsing and `AppSettings`**

Move the tested quote/comment-aware parser from the ATG config into `walletquota.config.read_env`. Define:

```python
@dataclass(frozen=True)
class AppSettings:
    values: Mapping[str, str] = field(repr=False)
    enabled_providers: Tuple[str, ...]
    report_directory: Path = Path("reports")

    @classmethod
    def load(cls, path: Path, enabled=None, output_dir=None):
        return cls.from_values(read_env(path), enabled, output_dir)
```

Normalize provider names to lowercase ASCII identifiers matching `^[a-z][a-z0-9_]*$`; deduplicate while preserving order. `ENABLED_PROVIDERS` defaults to `atgpay` only when the key is absent.
Copy `values` into `MappingProxyType(dict(values))` during construction so the frozen settings object cannot be mutated through its mapping.

- [ ] **Step 3: Write failing registry tests**

Assert registered providers retain requested order, a mixed registered/unknown list returns an `UnavailableProvider` at the unknown position, all-unknown raises `ConfigError`, and duplicate configured names execute once.

- [ ] **Step 4: Implement the explicit trusted registry**

```python
PROVIDER_FACTORIES = {
    "atgpay": lambda values, overrides: AtgPayProvider(values, overrides=overrides),
}


def build_providers(settings, factories=PROVIDER_FACTORIES, overrides=None):
    known_count = sum(name in factories for name in settings.enabled_providers)
    if known_count == 0:
        raise ConfigError("No enabled provider is registered")
    return tuple(
        factories[name](settings.values, (overrides or {}).get(name, {}))
        if name in factories
        else UnavailableProvider(name)
        for name in settings.enabled_providers
    )
```

`UnavailableProvider.run()` returns `ProviderResult.failure(name, "configuration", "Provider is not registered")`.

- [ ] **Step 5: Run focused and full tests**

```bash
python3 -m unittest tests.test_app_config tests.test_registry -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit configuration and registry**

```bash
git add walletquota/config.py walletquota/registry.py \
  tests/test_app_config.py tests/test_registry.py
git commit -m "feat: add explicit provider registry"
```

---

### Task 5: Run providers concurrently and aggregate trusted totals

**Files:**
- Create: `walletquota/runner.py`
- Create: `walletquota/aggregate.py`
- Create: `tests/test_runner.py`
- Create: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: ordered `Sequence[Provider]` and their standard results.
- Produces: `run_providers(providers, max_workers=8) -> Tuple[ProviderResult, ...]` and `aggregate_results(results, started_at, finished_at) -> AggregateResult`.

- [ ] **Step 1: Write failing concurrency and isolation tests**

Use two providers sharing a `threading.Barrier(2)` so sequential execution would hang. Also use one provider that raises `RuntimeError("secret raw body")`; assert the other succeeds, order follows input order, and the failed result is `error/internal` with message exactly `Unexpected provider failure`.

- [ ] **Step 2: Implement the bounded concurrent runner**

```python
def run_providers(providers, max_workers=8):
    if not providers:
        return ()
    worker_count = min(len(providers), max_workers)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(provider.run) for provider in providers]
        results = []
        for provider, future in zip(providers, futures):
            try:
                result = future.result()
                if result.provider != provider.name:
                    raise ValueError("Provider returned a mismatched name")
            except Exception:
                result = ProviderResult.failure(
                    provider.name, "internal", "Unexpected provider failure"
                )
            results.append(result)
    return tuple(results)
```

Validate `1 <= max_workers <= 8` and never interpolate the caught exception.

- [ ] **Step 3: Write failing aggregation tests**

Provide one `success`, one `partial` containing orders, and one `error`. Assert only success orders enter `total`, channel status counts are `1/1/1`, the overall status is `partial`, and channels remain in runner order. Also assert all-success is `success` and a non-INR injected fake is rejected.

- [ ] **Step 4: Implement trusted aggregation**

```python
trusted_orders = tuple(
    order
    for result in results
    if result.status == "success" and result.complete
    for order in result.orders
)
total = summarize_orders(trusted_orders)
total.update({
    "channel_success": sum(r.status == "success" for r in results),
    "channel_partial": sum(r.status == "partial" for r in results),
    "channel_error": sum(r.status == "error" for r in results),
})
status = "success" if all(r.status == "success" for r in results) else "partial"
return AggregateResult(status, tuple(results), total, started_at, finished_at)
```

Reject an empty result sequence because the registry must provide at least one channel.

- [ ] **Step 5: Run focused and full tests**

```bash
python3 -m unittest tests.test_runner tests.test_aggregate -v
python3 -m unittest discover -s tests -v
```

Expected: barrier concurrency, failure isolation, aggregation, and regression tests pass.

- [ ] **Step 6: Commit concurrent execution and aggregation**

```bash
git add walletquota/runner.py walletquota/aggregate.py \
  tests/test_runner.py tests/test_aggregate.py
git commit -m "feat: aggregate providers concurrently"
```

---

### Task 6: Generate terminal, aggregate JSON, and per-channel CSV output

**Files:**
- Create: `walletquota/reporting.py`
- Create: `tests/test_aggregate_reporting.py`

**Interfaces:**
- Consumes: `AggregateResult` and a parent report directory.
- Produces: `format_terminal_report(result) -> str` and `write_report_bundle(result, directory) -> ReportBundle`.

- [ ] **Step 1: Write failing terminal format tests**

Assert the output starts with `订单池综合报告`, contains `total:`, then `atgpay: success`, then `xxxpay: error` in that order. Assert total channel counts, INR amount, each successful channel distribution, and a safe error message appear. Assert partial orders are visible under their channel but excluded from `total`.

- [ ] **Step 2: Implement provider-neutral terminal formatting**

Reuse a single `_decimal_text` helper. Format `total` first and each channel in `AggregateResult.channels` order. For `success` and `partial`, call `summarize_orders(result.orders)`; for `error`, print only category and safe message.

- [ ] **Step 3: Write failing report bundle tests**

In a temporary directory, assert the first run creates:

```text
YYYYMMDD_HHMMSS/aggregate.json
YYYYMMDD_HHMMSS/atgpay_orders.csv
```

Assert an error channel has no CSV, partial with orders has a CSV, a second result with the same timestamp creates `_2`, and JSON has exact top-level keys `metadata`, `total`, `channels`. Recursively scan JSON and CSV content for configured sentinel secrets and assert none occur.

- [ ] **Step 4: Implement atomic bundle writing**

Define:

```python
@dataclass(frozen=True)
class ReportBundle:
    directory: Path
    aggregate_json: Path
    channel_csv: Mapping[str, Path]
```

Reserve the unique timestamp directory with `mkdir(exist_ok=False)`, trying suffixes `_2`, `_3`, and onward. Write each file to `.<name>-<random>` in the target directory, flush and close it, then call `os.replace`. Serialize every `Decimal` as a plain string. JSON channel objects use the provider name as their key and contain `status`, `complete`, `summary`, `metadata`, optional `error`, and `orders`.

- [ ] **Step 5: Run focused and full tests**

```bash
python3 -m unittest tests.test_aggregate_reporting -v
python3 -m unittest discover -s tests -v
```

Expected: all output contract, atomic write, secret scan, and regression tests pass.

- [ ] **Step 6: Commit reporting**

```bash
git add walletquota/reporting.py tests/test_aggregate_reporting.py
git commit -m "feat: write multi-provider report bundles"
```

---

### Task 7: Add the aggregate CLI and preserve the ATG compatibility entry

**Files:**
- Create: `wallet_quota.py`
- Modify: `atg_pool_report.py`
- Create: `tests/test_wallet_quota_cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `--env-file`, `--output-dir`, `--verbose`; compatibility entry also consumes `--sort-type`, `--order-type`, and `--page-size`.
- Produces: exit codes `0`, `1`, `2`, `3`, terminal aggregate output, and a report bundle path.

- [ ] **Step 1: Write failing aggregate CLI tests**

Patch `wallet_quota.build_providers` and `wallet_quota.write_report_bundle` with local fakes. Verify:

```python
self.assertEqual(main(["--env-file", str(env)]), 0)  # all success
self.assertEqual(main(["--env-file", str(env)]), 3)  # channel error
self.assertEqual(main(["--env-file", str(missing)]), 2)
```

Also assert a report write `OSError` returns `1`, verbose output contains provider names but no credentials, and the CLI always calls every configured provider.

- [ ] **Step 2: Implement `wallet_quota.py`**

The main flow is exact and linear:

```python
settings = AppSettings.load(args.env_file, output_dir=args.output_dir)
providers = build_providers(settings)
started_at = datetime.now(timezone.utc)
results = run_providers(providers)
aggregate = aggregate_results(results, started_at, datetime.now(timezone.utc))
bundle = write_report_bundle(aggregate, settings.report_directory)
print(format_terminal_report(aggregate), file=stdout)
print("综合 JSON: {}".format(bundle.aggregate_json.resolve()), file=stdout)
return 0 if aggregate.status == "success" else 3
```

Catch `ConfigError` as exit `2`; catch program-level `OSError` and invalid aggregate data as exit `1`. Provider-level failures must already be data and must not reach this handler.

- [ ] **Step 3: Convert the legacy entry to the aggregate pipeline**

Keep the old argument parser. Load `AppSettings` with `enabled=("atgpay",)`, pass its non-secret overrides under the `atgpay` key to `build_providers`, and use the same runner, aggregate, terminal, and report writers. Existing `.env` files using only `ATG_*` must continue to work.

- [ ] **Step 4: Update legacy CLI and end-to-end tests**

Replace expectations for old single CSV/JSON names with the report bundle contract. Retain tests for missing configuration, partial exit `3`, safe stderr, non-secret verbose progress, ATG signature, and full pagination.

- [ ] **Step 5: Run CLI and full tests**

```bash
python3 -m unittest tests.test_wallet_quota_cli tests.test_cli tests.test_end_to_end -v
python3 -m unittest discover -s tests -v
python3 wallet_quota.py --help
python3 atg_pool_report.py --help
```

Expected: all tests pass and both help commands exit `0` without network access.

- [ ] **Step 6: Commit the command-line migration**

```bash
git add wallet_quota.py atg_pool_report.py \
  tests/test_wallet_quota_cli.py tests/test_cli.py tests/test_end_to_end.py
git commit -m "feat: add multi-provider command line report"
```

---

### Task 8: Update configuration docs and run final verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `walletquota/__init__.py`

**Interfaces:**
- Consumes: the completed multi-provider CLI and configuration contract.
- Produces: copy-pasteable setup instructions and a verified release candidate.

- [ ] **Step 1: Update the configuration template**

Use these primary names while documenting legacy compatibility in README:

```dotenv
ENABLED_PROVIDERS=atgpay
ATGPAY_PHONE=
ATGPAY_PASSWORD=
ATGPAY_API_BASE=https://api.atg-game.com/app
ATGPAY_SORT_TYPE=1
ATGPAY_ORDER_TYPE=4
ATGPAY_PAGE_SIZE=10
ATGPAY_REQUEST_DELAY_MS=150
ATGPAY_TIMEOUT_SECONDS=30
REPORT_DIRECTORY=reports
```

- [ ] **Step 2: Rewrite README around the aggregate workflow**

Document `python3 wallet_quota.py`, the `total:` and channel output, report bundle tree, statuses and exits, provider registration steps, `ATG_*` fallback names, security boundaries, and the compatibility entry. Explicitly state that adding a provider requires an implementation under `walletquota/providers/`, local fake tests, and one explicit `PROVIDER_FACTORIES` registration.

- [ ] **Step 3: Run fresh verification**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/walletQuota-pyc python3 -m compileall -q \
  walletquota atg_pool wallet_quota.py atg_pool_report.py tests
python3 -m unittest discover -s tests -v
python3 wallet_quota.py --help
python3 atg_pool_report.py --help
python3 wallet_quota.py --env-file /private/tmp/walletQuota-missing.env
git diff --check
```

Expected: compile exits `0`; all tests pass; both help commands exit `0`; the missing config command exits `2` with a safe message; diff check is clean.

- [ ] **Step 4: Scan production output paths for secret persistence**

Run a local fixture execution using sentinel password `DO_NOT_PERSIST_SECRET_7f31`, then:

```bash
if rg -n "DO_NOT_PERSIST_SECRET_7f31|sessionKey|loginToken|Signature" reports; then
  exit 1
fi
if rg -n "DO_NOT_PERSIST_SECRET_7f31" walletquota wallet_quota.py atg_pool_report.py; then
  exit 1
fi
```

Expected: both searches print nothing and exit through the normal success path. Protocol field names may exist in the ATG client implementation, but none may appear in generated reports.

- [ ] **Step 5: Commit documentation and final cleanup**

```bash
git add .env.example README.md walletquota/__init__.py
git commit -m "docs: explain multi-provider reporting"
```

- [ ] **Step 6: Request independent code review before completion**

Review against `docs/superpowers/specs/2026-08-12-multi-provider-aggregate-design.md`. Resolve every Critical or Important issue with a failing test first, rerun the full verification commands, and report that real API calls remain untested until local credentials are supplied.
