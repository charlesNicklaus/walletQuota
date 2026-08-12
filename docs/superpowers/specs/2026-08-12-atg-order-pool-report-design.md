# ATG Order Pool Report — Design Specification

## Objective

Build a single-run Python command-line tool that signs in to ATG Pay through its normal API, retrieves every order currently visible in a selected order pool, and reports the pool size, aggregate amounts, and exact amount distribution.

The tool is read-only after authentication. It must not receive orders, submit payments or UTRs, bind accounts or cards, change settings, or call any other state-changing business endpoint.

## User workflow

1. Copy `.env.example` to `.env` and enter the ATG Pay mobile number and password locally.
2. Run `python atg_pool_report.py` whenever a fresh report is needed.
3. The tool logs in, fetches every result page, validates completeness, prints a concise report, and saves timestamped CSV and JSON artifacts.

Secrets remain local. `.env` is excluded from version control. Credentials, session keys, signatures, and authentication responses are never printed or written to reports.

## Confirmed API behavior

- API base URL: `https://api.atg-game.com/app`
- Login: `POST /auth/login`
- Order pool: `POST /pay/debit/task`
- Login request: `phone`, `password`
- Login response data: `userId`, `loginToken`, `sessionKey`
- Success envelope: `{ "code": 200, "message": "success", "data": ... }`
- Pool response data: `total`, `list`, `inrBuyConfig`, `inrTodayBuyCount`
- Confirmed defaults: `sortType=1`, `orderType=4`
- Authenticated POST signature: lowercase MD5 hex of `key1=value1&key2=value2&...&sessionKey`, with final body entries sorted lexicographically by key. Send it as the `Signature` header.
- Each authenticated body contains a fresh millisecond `ts` and authenticated `userId`.

## Architecture

The Python 3.9+ standard-library-only implementation has four focused units:

1. Configuration loads and validates local secrets plus non-secret pool settings.
2. API client owns login, signing, JSON HTTP calls, response validation, and bounded retries.
3. Pool collector reads sequential pages, deduplicates by `orderNo`, and verifies completeness.
4. Reporter calculates Decimal-based statistics, prints the summary, and writes CSV/JSON snapshots.

## Configuration

Required `.env` values:

```dotenv
ATG_PHONE=
ATG_PASSWORD=
```

Optional defaults:

```dotenv
ATG_API_BASE=https://api.atg-game.com/app
ATG_SORT_TYPE=1
ATG_ORDER_TYPE=4
ATG_PAGE_SIZE=10
ATG_REQUEST_DELAY_MS=150
ATG_TIMEOUT_SECONDS=30
```

Credentials are not accepted on the command line because shell history and process listings could expose them.

## Requests and session handling

The client uses one cookie-aware HTTP session and JSON headers compatible with the web client, including the web origin and referer. Login is unsigned. Login material is retained in memory only.

Every pool request contains `page`, `size`, `sortType`, `orderType`, `ts`, and `userId`. The signature is calculated from the final body. An invalid or refresh-required session response causes one clean re-login and one retry; the program does not implement an unverified refresh contract.

The program never attempts to bypass Cloudflare, CAPTCHA, authentication, or authorization controls.

## Complete pagination

The collector reads page 1 onward and treats the first response's `total` as the expected count. It stops when collected unique orders reach `total`, the API returns an empty/short page, or a defensive page cap is reached. Duplicate `orderNo` values are ignored.

If `total` changes between pages, the report records that the pool changed during collection. A snapshot is complete only when unique collected orders equal the latest API total. An incomplete run exits nonzero and still saves a clearly marked partial JSON/CSV snapshot.

## Metrics and output

Money uses `decimal.Decimal`. The terminal summary contains:

- local and UTC snapshot timestamps;
- selected `sortType` and `orderType`;
- API total and unique count collected;
- total, minimum, maximum, mean, and median `orderAmount`;
- total `income` and total `balance` where available;
- exact amount distribution sorted by amount: amount, order count, percentage, and subtotal;
- completeness and pool-stability status.

Each run writes:

- `reports/atg_pool_YYYYMMDD_HHMMSS.csv`: one normalized row per order;
- `reports/atg_pool_YYYYMMDD_HHMMSS.json`: metadata, summary, distribution, and orders.

Incomplete snapshots add `_partial` before the extension in both filenames.

Reports contain no credentials or reusable authentication material.

## Failure handling

- Invalid configuration fails before network access with an actionable message.
- Transient network and rate-limit errors receive a small bounded retry with backoff and `Retry-After` support.
- HTTP errors, invalid JSON, API errors, and schema mismatches fail clearly without printing secrets.
- API session codes `1001` and `1002` trigger at most one re-login and retry per request.
- Incomplete pagination saves partial evidence and returns a nonzero exit code.

## Command interface

```bash
python atg_pool_report.py
python atg_pool_report.py --sort-type 1 --order-type 4 --page-size 10
```

Only non-secret reporting/pool parameters may be overridden. There is no arbitrary endpoint or state-changing operation.

## Verification

Built-in `unittest` tests cover configuration, deterministic signing, API envelopes, retry/re-login behavior, multi-page collection, deduplication, changing totals, Decimal metrics, output files, and secret exclusion. A live smoke test is optional and runs only after the user creates `.env`; it calls login and the read-only pool endpoint only.

## Success criteria

- One command logs in and produces a current pool report.
- Statistics use all retrieved orders, not a sample page.
- Complete reports reconcile unique orders with the API `total`.
- Terminal, CSV, and JSON contain the requested counts and amount distribution.
- No secret is printed, written to reports, or passed through command-line arguments.
- Only login and the read-only order-pool endpoint are called.
