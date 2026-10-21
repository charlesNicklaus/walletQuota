# Continuous PostgreSQL Capture Implementation Plan

**Goal:** Deliver a start script for continuous read-only collection and automatic PostgreSQL imports on Mac Studio.

**Architecture:** Keep the existing locked 20-minute scheduler and add an explicit PostgreSQL cycle callback. A thin shell launcher detaches the worker; the one-cycle entry point owns collection, retained exports and retries.

**Tech stack:** Python 3.9+ standard library, bash, existing psql, optional macOS caffeinate.

- [x] Add failing scheduler dispatch tests; implement `--postgres` and `--include-hoyo` while preserving default query behavior and current locking.
- [x] Implement `wallet_quota_pipeline.py` with tests for independent channels, Hoyo, trusted totals, private SQL/markers, safe libpq environment and retry-on-next-cycle behavior.
- [x] Implement executable `start.sh` with isolated fake-process tests for arbitrary working directory, foreground/background behavior, logs, PID and dependencies.
- [x] Document local credentials, PostgreSQL settings, start/log/stop commands and historical data retention.
- [x] Independently review the workflow and pass all 254 offline tests, including two scheduled cycles with a failed first import and successful retry; prepare the explicit source changes for publication.
