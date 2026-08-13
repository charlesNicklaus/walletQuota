# Standalone Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manually started Python process that queries walletQuota at IST `:00/:20/:40`, keeps the existing manual CLI, and removes only confirmed walletQuota exports from prior IST dates.

**Architecture:** Report ownership and deletion live in a small `retention` module with no scheduler dependencies. A separate scheduler module owns IST time calculations, process locking, loop sequencing, and calls the existing `wallet_quota.main()` rather than duplicating query logic; a thin top-level script exposes the CLI.

**Tech Stack:** Python 3.9+ standard library, `zoneinfo`, `fcntl`, `signal`, `shutil`, `unittest`.

## Global Constraints

- Do not use Codex automation, cron, `launchd`, or boot auto-start.
- Start manually; run immediately, then at IST `:00`, `:20`, and `:40`.
- Never backfill missed runs and never run overlapping queries.
- Delete only direct child directories of the configured report root that are confirmed walletQuota exports and whose `finished_at` IST date is earlier than today.
- Never follow symlinks or delete `.env`, source code, Git data, the report root, files, unknown directories, current-day reports, or future reports.
- Preserve the existing `python3 wallet_quota.py --verbose` manual command.
- A query exit code of `0`, `1`, `2`, or `3` must not terminate an already-running scheduler; startup configuration and lock errors are fatal.
- Tests use fake clocks, fake query functions, and temporary directories; they must not access the real payment API or wait 20 minutes.
- Work directly on the user-approved `main` branch; do not push to the remote.

---

### Task 1: Mark generated reports and implement safe IST retention

**Files:**
- Create: `walletquota/retention.py`
- Modify: `walletquota/reporting.py`
- Create: `tests/test_retention.py`
- Modify: `tests/test_aggregate_reporting.py`

**Interfaces:**
- Produces: `CleanupResult(deleted: Tuple[Path, ...], preserved: Tuple[Path, ...], warnings: Tuple[str, ...])`.
- Produces: `cleanup_expired_reports(report_directory: Path, today_ist: date) -> CleanupResult`.
- Adds: aggregate metadata `generator == "walletQuota"` and `schema_version == 1`.

- [ ] **Step 1: Write failing report-marker and deletion tests**

Add a report test asserting:

```python
payload = json.loads(bundle.aggregate_json.read_text(encoding="utf-8"))
self.assertEqual(payload["metadata"]["generator"], "walletQuota")
self.assertEqual(payload["metadata"]["schema_version"], 1)
```

Create retention tests that build temporary direct-child snapshot directories and assert:

- marked exports from yesterday are deleted;
- marked exports from today/future are preserved;
- valid legacy walletQuota exports from yesterday are deleted;
- corrupted JSON, unknown structures, invalid names, ordinary files and symlinks are preserved;
- a symlink targeting a directory outside the report root leaves the target untouched;
- a nested valid-looking report below an unknown direct child is not scanned or deleted.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_retention tests.test_aggregate_reporting -v
```

Expected: FAIL because the generator marker and retention module do not exist.

- [ ] **Step 3: Add explicit generator metadata**

Extend `write_report_bundle()` metadata with exactly:

```python
"generator": "walletQuota",
"schema_version": 1,
```

- [ ] **Step 4: Implement strict candidate recognition**

In `walletquota/retention.py`, define:

```python
SNAPSHOT_DIRECTORY = re.compile(r"^\d{8}_\d{6}(?:_(?:[2-9]|[1-9]\d+))?$")
IST = ZoneInfo("Asia/Kolkata")
```

Accept a marked report only when `metadata.generator == "walletQuota"`, `metadata.schema_version == 1`, and `metadata.finished_at` parses to an aware datetime. Accept an unmarked legacy report only when its top-level keys are exactly `metadata`, `total`, `channels`; its metadata contains `status`, `started_at`, `finished_at`, `provider_count`; every channel contains `status`, `complete`, `summary`, `metadata`, `orders`; and recursively normalized keys contain no credential/raw-auth names.

- [ ] **Step 5: Implement guarded deletion**

Iterate only `report_directory.iterdir()`. Reject symlinks before `is_dir()`, require `aggregate.json` to be a regular non-symlink file, require `candidate.resolve().parent == report_directory.resolve()`, and call `shutil.rmtree(candidate)` only after recognition and the IST date comparison. Return fixed warnings such as `"Unrecognized report directory preserved"` and `"Report directory could not be deleted"` without including report contents.

- [ ] **Step 6: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_retention tests.test_aggregate_reporting -v
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: focused and full suites pass; diff check is clean.

- [ ] **Step 7: Commit Task 1**

```bash
git add walletquota/retention.py walletquota/reporting.py \
  tests/test_retention.py tests/test_aggregate_reporting.py
git commit -m "feat: add safe report retention"
```

---

### Task 2: Implement IST schedule calculations and process locking

**Files:**
- Create: `walletquota/scheduler.py`
- Create: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `next_run_after(now: datetime) -> datetime`.
- Produces: `SchedulerLock.acquire(report_directory: Path) -> SchedulerLock`, with context-manager release.
- Produces: `StandaloneScheduler(report_directory, query, cleanup, clock, wait, stdout, stderr).run()`.

- [ ] **Step 1: Write failing schedule tests**

Use aware IST datetimes and assert:

```python
self.assertEqual(next_run_after(ist(12, 0)), ist(12, 20))
self.assertEqual(next_run_after(ist(12, 19, 59)), ist(12, 20))
self.assertEqual(next_run_after(ist(12, 40)), ist(13, 0))
self.assertEqual(next_run_after(ist(23, 40)), next_day_ist(0, 0))
```

Also reject naive datetimes.

- [ ] **Step 2: Write failing lock and loop-order tests**

Assert a second `SchedulerLock` for the same report root raises `SchedulerAlreadyRunning`, release permits reacquisition, and the lock filename lives beside—not inside—the report root.

Use fake `clock`, `wait`, `query`, and `cleanup` functions to assert:

- startup order is `cleanup`, then `query`;
- same-day cycles call only `query`;
- crossing IST midnight calls `cleanup`, then `query`;
- query return codes `0`, `1`, `2`, and `3` are recorded but do not terminate the loop;
- after a simulated slow query, the next wait target is strictly in the future and no missed run is replayed;
- a stop request exits without another query.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_scheduler -v`

Expected: FAIL because `walletquota.scheduler` does not exist.

- [ ] **Step 4: Implement the schedule calculation**

Convert `now` to `Asia/Kolkata`, round to the next strict 20-minute boundary, and return an aware IST datetime. Do not accept naive inputs.

- [ ] **Step 5: Implement non-blocking process lock**

Create the report root when needed; choose `report_directory.resolve().parent / ("." + report_directory.name + ".wallet_quota_scheduler.lock")`; open it with mode `a+`; acquire `fcntl.LOCK_EX | fcntl.LOCK_NB`; keep the handle alive; release with `LOCK_UN` and close. Never delete the lock file, because removing an active flock path can permit a second inode/lock.

- [ ] **Step 6: Implement the scheduler loop**

On `run()`, call cleanup for `clock().astimezone(IST).date()`, then query once. Track the last cleaned date. Before every later query, clean again only if the IST date changed. After every query, recompute `next_run_after(clock())`; `wait(target)` must be injectable and return `False` when stopped. Catch ordinary query exceptions into a fixed safe stderr message, but allow `KeyboardInterrupt` and `SystemExit` to stop.

- [ ] **Step 7: Run focused and full tests**

```bash
python3 -m unittest tests.test_scheduler -v
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: all tests pass and diff check is clean.

- [ ] **Step 8: Commit Task 2**

```bash
git add walletquota/scheduler.py tests/test_scheduler.py
git commit -m "feat: add IST report scheduler core"
```

---

### Task 3: Add the standalone CLI and preserve manual querying

**Files:**
- Create: `wallet_quota_scheduler.py`
- Create: `tests/test_scheduler_cli.py`
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `wallet_quota_scheduler.main(argv=None, stdout=sys.stdout, stderr=sys.stderr) -> int`.
- Consumes: `AppSettings.load()`, `wallet_quota.main()`, `StandaloneScheduler`, `SchedulerLock`, and `cleanup_expired_reports`.

- [ ] **Step 1: Write failing CLI wiring tests**

Patch configuration, lock, scheduler, and query boundaries to assert:

- parser supports only `--env-file`, `--output-dir`, and `--verbose`;
- resolved report directory and exact manual-query arguments are forwarded;
- cleanup receives the configured report directory;
- lock conflict and configuration error return nonzero without traceback or credentials;
- `KeyboardInterrupt`/SIGTERM-style stop returns `0` and releases the context-managed lock;
- the existing `wallet_quota.main()` manual CLI test remains green.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_scheduler_cli tests.test_wallet_quota_cli -v
```

Expected: FAIL because `wallet_quota_scheduler.py` does not exist.

- [ ] **Step 3: Implement the thin entrypoint**

Load `AppSettings` before acquiring the lock so startup config errors are fatal. Build query arguments as:

```python
query_args = ["--env-file", str(args.env_file)]
if args.output_dir is not None:
    query_args.extend(["--output-dir", str(args.output_dir)])
if args.verbose:
    query_args.append("--verbose")
```

Pass `lambda: wallet_quota.main(query_args, stdout, stderr)` into `StandaloneScheduler`. Pass `lambda today: cleanup_expired_reports(settings.report_directory, today)` as cleanup and print only counts/fixed warnings. Acquire `SchedulerLock` for the full `run()` call. Catch `ConfigError` and `SchedulerAlreadyRunning` with safe messages; catch `KeyboardInterrupt` and return `0`.

- [ ] **Step 4: Document manual operation**

Add README commands:

```bash
python3 wallet_quota_scheduler.py --verbose
# Stop with Ctrl+C

python3 wallet_quota.py --verbose
```

Document IST `:00/:20/:40`, immediate first run, no backfill/overlap, human restart, report-only cleanup, manual reports included, no Codex/cron/launchd/boot startup, and that unknown directories are preserved.

Ignore only the lock filename pattern:

```gitignore
.*.wallet_quota_scheduler.lock
```

- [ ] **Step 5: Run complete verification**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/walletQuota-scheduler-pyc \
  python3 -m compileall -q walletquota wallet_quota.py wallet_quota_scheduler.py tests
python3 -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: compilation and all tests pass; only intentional changes remain.

- [ ] **Step 6: Run a bounded local dry integration**

Use a fake query and stop-aware fake wait to execute startup cleanup/query plus one scheduled cycle in a temporary report root. Assert no path outside the temporary root changes and no real API is called.

- [ ] **Step 7: Commit Task 3**

```bash
git add wallet_quota_scheduler.py tests/test_scheduler_cli.py README.md .gitignore
git commit -m "feat: add standalone wallet scheduler"
```

---

## Plan self-review

- Spec coverage: scheduling, manual startup/restart, non-overlap, safe cleanup, legacy recognition, locking, logging, stop behavior, manual CLI preservation, and no external scheduler all map to Tasks 1–3.
- Placeholder scan: every step contains concrete commands, interfaces, and expected results.
- Type consistency: the cleanup, scheduler, lock, and CLI interfaces are defined once and used consistently across tasks.
- Security review target: retention deletion guards and report recognition require dedicated task review before scheduler integration.
