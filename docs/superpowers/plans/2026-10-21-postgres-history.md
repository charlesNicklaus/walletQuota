# PostgreSQL History Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the independent scheduler and schema tasks; the root implements the exporter and verifies the complete change.

**Goal:** Remove automatic report deletion and deliver PostgreSQL tables plus a local import file.

**Architecture:** Keep the scheduler's existing query boundary, remove retention calls, and export validated report models to four PostgreSQL tables. Exact money, JSONB metadata and deterministic batch identity preserve report history.

**Tech Stack:** Python 3.9+ standard library, PostgreSQL, unittest; temporary PostgreSQL engine for validation only.

## Tasks

- [x] Add a failing scheduler regression proving yesterday's report survives startup and the current report survives midnight; remove cleanup callback, date tracking, retention module and obsolete tests. Run `python3 -m unittest tests.test_scheduler tests.test_scheduler_cli -v`.
- [x] Create `sql/schema.postgresql.sql` and `sql/README.md` for four tables, indexes, and trusted totals view, with no cascading deletion or cleanup jobs.
- [x] Add failing CLI tests in `tests/test_export_postgres.py`, then implement `scripts/export_postgres.py`; validate repeatable identity, exact decimals, SQL quoting, safe errors, local file permissions and source preservation.
- [x] Apply schema and synthetic exports twice in a temporary PostgreSQL engine; verify trusted totals, row counts, timestamps, constraints and string round trips, including both string-escape session settings.
- [x] Export the latest four-channel report into ignored local SQL; import it twice in a temporary PostgreSQL engine and verify the expected row count and exact trusted total.
- [x] Run the full 235-test suite, independently review the change and check the explicit source-file set for private data, preparing the main-branch commit and push.
