# Timestamped Capture Files

**Goal:** Include each capture's local completion time in JSON, order CSV, SQL and import marker filenames so exported files remain identifiable outside their report directory.

**Design:** Keep the existing reserved `YYYYMMDD_HHMMSS` directory and its collision suffix. Use that directory name as the shared filename suffix: `aggregate_<stamp>.json`, `<provider>_orders_<stamp>.csv`, `postgres_<stamp>.data.sql`, and `postgres_<stamp>.pending` / `.imported`. Preserve JSON contents, private file permissions, historical files, the scheduler and the stable process log. The import queue must accept both timestamped markers and existing `postgres.pending` batches; derive SQL and imported paths from each marker's basename.

- [x] Update reporting filename tests, including same-second captures and merge consumers; verify failure before implementing the names.
- [x] Update pipeline and bounded scheduler tests for timestamped artifacts; add coverage for an existing pending batch surviving the upgrade and unsafe timestamped symlinks.
- [x] Generate timestamped SQL and markers and drain both queue naming formats without changing hash validation or history retention.
- [x] Update operator examples, independently review compatibility, pass all 257 offline tests and prepare the explicit changes for publication.
