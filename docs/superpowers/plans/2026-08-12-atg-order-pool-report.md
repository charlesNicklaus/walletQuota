# ATG Order Pool Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe, on-demand Python CLI that logs into ATG Pay, reads every page of the configured order pool, and reports order counts and exact amount distribution to the terminal, CSV, and JSON.

**Architecture:** A standard-library package separates local configuration, authenticated HTTP/signing, pagination, and reporting. The top-level CLI composes these units, retains secrets in memory, and calls only login plus the read-only order-pool endpoint.

**Tech Stack:** Python 3.9+, standard library (`argparse`, `csv`, `decimal`, `hashlib`, `http.cookiejar`, `json`, `unittest`, `urllib`)

## Global Constraints

- Required runtime: Python 3.9 or newer.
- Runtime dependencies: Python standard library only.
- Default API base: `https://api.atg-game.com/app`.
- Default pool: `sortType=1`, `orderType=4`, page size 10, matching the confirmed browser request.
- Credentials are loaded from `.env`, never command-line arguments.
- Only `POST /auth/login` and `POST /pay/debit/task` may be called.
- Reports must exclude password, `loginToken`, `sessionKey`, signatures, and cookies.
- All monetary calculations use `decimal.Decimal`.

---

## File map

- `atg_pool_report.py`: CLI entry point and exit-code handling.
- `atg_pool/config.py`: `.env` parsing and validated settings.
- `atg_pool/client.py`: JSON transport, login, signing, retry, and session recovery.
- `atg_pool/pool.py`: order normalization, pagination, deduplication, and completeness.
- `atg_pool/report.py`: Decimal metrics, terminal rendering, CSV/JSON output.
- `tests/`: unit tests and fake transports.
- `.env.example`, `.gitignore`, `README.md`: secure setup and usage.

### Task 1: Configuration and signing primitives

**Files:**
- Create: `atg_pool/__init__.py`
- Create: `atg_pool/config.py`
- Create: `atg_pool/client.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Create: `tests/test_client.py`

**Interfaces:**
- Produces: `Settings.load(path: Path) -> Settings`
- Produces: `make_signature(session_key: str, payload: Mapping[str, object]) -> str`
- Produces: `APIError`, `AuthenticationError`, and `Transport` protocol used by later tasks.

- [ ] **Step 1: Write failing configuration tests**

Test comments, quoting, required values, integer validation, and safe `repr(Settings)` in `tests/test_config.py`.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `python -m unittest tests.test_config -v`
Expected: import failure because `atg_pool.config` does not exist.

- [ ] **Step 3: Implement immutable settings and `.env` parser**

Create a frozen `Settings` dataclass with `phone`, hidden `password`, API base, pool selectors, page size, delay, timeout, report directory, plus validated CLI override support.

- [ ] **Step 4: Write and run failing signature tests**

Assert lexicographic sorting, stable scalar stringification, and the known digest produced by `hashlib.md5(canonical.encode()).hexdigest()`.

- [ ] **Step 5: Implement signature and error/transport interfaces**

Implement `make_signature()` without logging its canonical input because it includes the session key.

- [ ] **Step 6: Run task tests**

Run: `python -m unittest tests.test_config tests.test_client -v`
Expected: all Task 1 tests pass.

### Task 2: Authenticated API client

**Files:**
- Modify: `atg_pool/client.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Consumes: `Settings`, `make_signature()`.
- Produces: `AtgClient.login() -> None`
- Produces: `AtgClient.fetch_pool_page(page: int, size: int, sort_type: int, order_type: int) -> dict[str, object]`

- [ ] **Step 1: Add failing client tests**

Use a scripted fake transport to verify unsigned login, extraction of `userId/loginToken/sessionKey`, signed pool bodies, fresh timestamps, envelope validation, bounded transient retry, and one re-login for API codes 1001/1002.

- [ ] **Step 2: Run tests and verify the new failures**

Run: `python -m unittest tests.test_client -v`
Expected: failures for missing `AtgClient` behavior.

- [ ] **Step 3: Implement cookie-aware standard-library transport**

Use `urllib.request.build_opener(HTTPCookieProcessor(CookieJar()))`, JSON serialization, explicit timeout, fixed origin/referer, and `HTTPError`/`URLError` conversion to non-secret exceptions.

- [ ] **Step 4: Implement login, signed pool reads, retry, and re-login**

Restrict endpoints to constants in the client. Retry transient transport failures and 429/5xx responses at most twice. On API 1001/1002, log in once and repeat the pool request once.

- [ ] **Step 5: Run task tests**

Run: `python -m unittest tests.test_client -v`
Expected: all client tests pass.

### Task 3: Complete pool collection

**Files:**
- Create: `atg_pool/pool.py`
- Create: `tests/test_pool.py`

**Interfaces:**
- Consumes: an object implementing `fetch_pool_page(...)`.
- Produces: `Order` dataclass.
- Produces: `PoolSnapshot` dataclass with expected total, unique orders, completion, stability, page count, and timestamps.
- Produces: `collect_pool(client, settings) -> PoolSnapshot`.

- [ ] **Step 1: Write failing pagination tests**

Cover multiple pages, short final page, duplicate `orderNo`, an API total that changes, empty early page, malformed rows, and the defensive page cap.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m unittest tests.test_pool -v`
Expected: import failure because `atg_pool.pool` does not exist.

- [ ] **Step 3: Implement Decimal order normalization and collection**

Normalize monetary fields from `str(value)`, request sequential pages, sleep only between necessary pages, deduplicate, track every observed total, and set `complete = len(orders) == latest_total`.

- [ ] **Step 4: Run task tests**

Run: `python -m unittest tests.test_pool -v`
Expected: all pool tests pass.

### Task 4: Metrics and report artifacts

**Files:**
- Create: `atg_pool/report.py`
- Create: `tests/test_report.py`

**Interfaces:**
- Consumes: `PoolSnapshot` and normalized `Order` values.
- Produces: `build_summary(snapshot) -> dict[str, object]`
- Produces: `format_terminal_report(snapshot, summary) -> str`
- Produces: `write_reports(snapshot, summary, directory) -> tuple[Path, Path]`

- [ ] **Step 1: Write failing report tests**

Assert total/min/max/mean/median, total income/balance, exact amount buckets, percentage/subtotal, empty-pool behavior, deterministic CSV columns, JSON Decimal serialization, and absence of secret field names.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m unittest tests.test_report -v`
Expected: import failure because `atg_pool.report` does not exist.

- [ ] **Step 3: Implement metrics and terminal output**

Use Decimal arithmetic and median over sorted amounts. Render a readable fixed-width amount distribution without truncating buckets.

- [ ] **Step 4: Implement atomic CSV and JSON writers**

Write temporary sibling files and replace final timestamped targets only after successful serialization. JSON monetary values are decimal strings.

- [ ] **Step 5: Run task tests**

Run: `python -m unittest tests.test_report -v`
Expected: all report tests pass.

### Task 5: CLI, secure defaults, and documentation

**Files:**
- Create: `atg_pool_report.py`
- Create: `.env.example`
- Create: `.gitignore`
- Modify: `README.md`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: all package interfaces from Tasks 1–4.
- Produces: `main(argv: Sequence[str] | None = None) -> int` and the documented user workflow.

- [ ] **Step 1: Write failing CLI tests**

Patch composition boundaries and verify successful output/paths, configuration error exit 2, API error exit 1, incomplete snapshot exit 3, and that parser help exposes no secret arguments.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m unittest tests.test_cli -v`
Expected: import failure because `atg_pool_report` does not exist.

- [ ] **Step 3: Implement CLI composition and exit codes**

Support only `--env-file`, `--sort-type`, `--order-type`, `--page-size`, `--output-dir`, and `--verbose`. Print paths after artifacts are saved; verbose mode prints progress but never request bodies or auth data.

- [ ] **Step 4: Add safe setup files and complete README**

Ignore `.env`, report outputs, bytecode, caches, and virtual environments. Document setup, commands, fields, exit codes, security boundaries, and troubleshooting.

- [ ] **Step 5: Run complete test suite**

Run: `python -m unittest discover -s tests -v`
Expected: all tests pass.

### Task 6: End-to-end fixture verification and final audit

**Files:**
- Create: `tests/fixtures/pool_page_1.json`
- Create: `tests/test_end_to_end.py`
- Modify only if verification finds a defect: implementation or documentation files above.

**Interfaces:**
- Consumes: public CLI/package interfaces.
- Produces: reproducible fixture-backed proof that a 299-order response contract is interpreted correctly without live credentials.

- [ ] **Step 1: Add a sanitized response fixture and failing end-to-end test**

Use the confirmed response shape with `total=299` and sample orders. Verify envelope parsing, pool metadata, and distribution calculations through the public composition boundaries.

- [ ] **Step 2: Run the end-to-end test**

Run: `python -m unittest tests.test_end_to_end -v`
Expected: pass after Tasks 1–5; fix any integration mismatch surgically.

- [ ] **Step 3: Run syntax, test, and secret scans**

Run: `python -m compileall -q atg_pool atg_pool_report.py tests`
Expected: exit 0.

Run: `python -m unittest discover -s tests -v`
Expected: all tests pass.

Run: `git grep -nE '(loginToken|sessionKey|ATG_PASSWORD)=' -- ':!docs/**' ':!.env.example' || true`
Expected: no credential assignment containing a real value.

- [ ] **Step 4: Review the final diff and repository status**

Run: `git diff --check && git status --short`
Expected: no whitespace errors; only intended project files are changed.
