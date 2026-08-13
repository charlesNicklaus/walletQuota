# Account and Amount-Range Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fixed amount-range statistics and safely readable ATG receiving-account/UPI data to every comprehensive report without ever claiming an order.

**Architecture:** Extend the provider-neutral result model with immutable accounts, teach the ATG client only the four additional read endpoints, and let the ATG provider collect account data as best-effort enrichment after the existing trusted pool snapshot. Reporting uses full account values only in local JSON and a dedicated masking formatter for terminal output.

**Tech Stack:** Python 3.9+ standard library, `dataclasses`, `decimal.Decimal`, `hashlib`, `urllib`, `unittest`.

## Global Constraints

- Python 3.9 or newer; no third-party runtime dependencies.
- Amount ranges are exactly `<100`, `100 <= x < 10000`, `10000 <= x < 20000`, `20000 <= x <= 50000`, and `>50000`.
- Never call `/pay/receive/task`, payment, UTR submission, account mutation, or any other write endpoint.
- Terminal output must mask account numbers and UPI values; local `aggregate.json` keeps full account data.
- Reports must never include passwords, login tokens, session keys, cookies, signatures, or raw authentication responses.
- Account enrichment failures must not discard a complete order-pool result.
- Tests use local fake transports and must never access the real payment API.
- Work directly on the user-approved `main` branch and preserve unrelated worktree changes.

---

### Task 1: Replace exact-value distribution with fixed ranges

**Files:**
- Modify: `walletquota/summary.py`
- Modify: `tests/test_summary.py`
- Modify: `tests/test_aggregate_reporting.py`

**Interfaces:**
- Consumes: `Sequence[Order]` with finite INR `Decimal` amounts.
- Produces: `summarize_orders(orders)["amount_distribution"]` containing five stable bucket dictionaries.

- [ ] **Step 1: Write failing boundary and empty-range tests**

Add tests with amounts `99.99`, `100`, `9999.99`, `10000`, `19999.99`, `20000`, `50000`, and `50000.01`. Assert counts `[1, 2, 2, 2, 1]`, that bucket subtotals sum to `order_amount`, and that empty input still returns five zero rows.

- [ ] **Step 2: Run tests and verify the current exact-value distribution fails**

Run: `python3 -m unittest tests.test_summary tests.test_aggregate_reporting -v`

Expected: FAIL because existing rows are keyed by exact `amount` and empty input returns no rows.

- [ ] **Step 3: Implement the five fixed bucket definitions**

Define one immutable module-level tuple containing key, label, bounds, and inclusivity. Build rows in that order and calculate `count`, `percentage`, and `subtotal` with the existing two-decimal percentage rule.

- [ ] **Step 4: Update terminal distribution formatting**

Render each row as `范围 <label> × <count>（<percentage>%，小计 ₹<subtotal>）`; do not special-case an empty list because the summary always contains five rows.

- [ ] **Step 5: Run focused tests**

Run: `python3 -m unittest tests.test_summary tests.test_aggregate_reporting -v`

Expected: all focused tests pass.

---

### Task 2: Add provider-neutral account models and report serialization

**Files:**
- Modify: `walletquota/models.py`
- Modify: `walletquota/reporting.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_aggregate_reporting.py`

**Interfaces:**
- Produces: `Account(provider, kind, account_id, account=None, upi=None, bank_name=None, ifsc=None, order_id=None, attributes={})`.
- Extends: `ProviderResult.accounts: Tuple[Account, ...]`, with class methods accepting optional `accounts=()`.
- Produces: JSON `account_summary` and `accounts`, plus terminal-only masked rendering.

- [ ] **Step 1: Write failing account model tests**

Assert accepted kinds, at least one of account/UPI, immutable attributes, provider consistency inside `ProviderResult`, and empty accounts for failure results.

- [ ] **Step 2: Verify model tests fail for the missing type**

Run: `python3 -m unittest tests.test_models -v`

Expected: FAIL because `Account` and `ProviderResult.accounts` do not exist.

- [ ] **Step 3: Implement the immutable model**

Add `ACCOUNT_KINDS`, `Account`, and the new result field while preserving all existing `ProviderResult.success`, `partial`, and `failure` call sites through an optional final parameter.

- [ ] **Step 4: Write failing JSON and terminal masking tests**

Use a full UPI such as `alice123@paytm` and bank account `123456789012`. Assert full values exist in parsed `aggregate.json`, full values do not occur in terminal output, and masked distinguishable forms do occur.

- [ ] **Step 5: Implement account summary, JSON mapping, and masking**

Add private reporting helpers for account serialization, kind counts, generic account-number masking, UPI local-part/handle masking, and terminal lines. Keep accounts out of CSV.

- [ ] **Step 6: Run model and reporting tests**

Run: `python3 -m unittest tests.test_models tests.test_aggregate_reporting -v`

Expected: all focused tests pass.

---

### Task 3: Add allowlisted signed ATG read methods

**Files:**
- Modify: `walletquota/providers/atgpay/client.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Produces: `fetch_bound_upis(account_type=1)`, `fetch_bank_cards()`, `fetch_my_tasks_page(page, size)`, and `fetch_order_detail(order_no)` returning validated data mappings.

- [ ] **Step 1: Write failing fake-transport tests for all four reads**

Assert the exact paths, payload fields, final-payload signature, automatic login requirement, and authentication retry behavior. Also assert an arbitrary endpoint remains rejected.

- [ ] **Step 2: Verify focused tests fail**

Run: `python3 -m unittest tests.test_client -v`

Expected: FAIL because the read methods and allowlisted paths do not exist.

- [ ] **Step 3: Implement a private generic signed-read helper**

Build a fresh payload on every attempt, add `ts` and `userId`, sign it, call `_post_with_retry`, validate the envelope, and relog once on session failure. Keep `_url` restricted to a literal set containing only login, pool, and the four new read paths.

- [ ] **Step 4: Implement the four thin public methods**

Each method supplies only its endpoint-specific parameters and performs no mutation. Reject empty order numbers and invalid pagination locally.

- [ ] **Step 5: Run client tests**

Run: `python3 -m unittest tests.test_client -v`

Expected: all tests pass.

---

### Task 4: Collect and normalize ATG accounts as best-effort enrichment

**Files:**
- Create: `walletquota/providers/atgpay/accounts.py`
- Modify: `walletquota/providers/atgpay/provider.py`
- Modify: `tests/test_atg_provider.py`
- Create: `tests/test_atg_accounts.py`

**Interfaces:**
- Consumes: the four ATG client read methods.
- Produces: `AccountSnapshot(accounts, warnings, bound_upi_total, bank_card_total, claimed_task_total)` and provider metadata counts.

- [ ] **Step 1: Write failing mapping and deduplication tests**

Cover bound UPI keys `id/account/upi/type/sale/status`, bank keys `id/cardNumber/accName/ifsc/bankName`, claimed task pagination, detail keys `toUpi/cardNumber/bankName/ifsc/accountName`, duplicate rows, and empty responses.

- [ ] **Step 2: Verify account collector tests fail**

Run: `python3 -m unittest tests.test_atg_accounts -v`

Expected: FAIL because the collector module does not exist.

- [ ] **Step 3: Implement deterministic safe normalization**

Use explicit field allowlists. Derive missing `account_id` with SHA-256 over provider/kind/account/UPI/order ID and keep only a short hex digest. Fetch claimed-task pages until collected rows reach `total`; cap pages at 1000 and reject contradictory pagination as a safe warning.

- [ ] **Step 4: Write failing provider integration tests**

Assert successful pool orders and accounts are returned together, account failure leaves provider `success` with `metadata.account_warnings`, and authentication/data failures are converted to safe warnings without raw response text.

- [ ] **Step 5: Integrate one shared logged-in client into ATG collection**

Change `_collect` to return both the pool snapshot and account snapshot from the same authenticated client. Preserve injectable test seams so existing fake snapshot tests require only a minimal default empty account snapshot.

- [ ] **Step 6: Run ATG account and provider tests**

Run: `python3 -m unittest tests.test_atg_accounts tests.test_atg_provider -v`

Expected: all focused tests pass.

---

### Task 5: Update docs and verify the whole application

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `tests/test_end_to_end.py`

**Interfaces:**
- Produces: documented report semantics and end-to-end proof that comprehensive output includes stable ranges and accounts.

- [ ] **Step 1: Write a failing end-to-end output test**

Assert five range rows appear under `total` and `atgpay`, terminal account values are masked, and `aggregate.json` contains full accounts under `channels.atgpay.accounts`.

- [ ] **Step 2: Run the end-to-end test and verify failure**

Run: `python3 -m unittest tests.test_end_to_end -v`

Expected: FAIL until all new output fields are wired.

- [ ] **Step 3: Document usage and sensitive-output behavior**

Explain range boundaries, account sources, zero-account meaning, JSON sensitivity, and the guarantee that the program never claims an order. Add `.firecrawl/` to `.gitignore` because it contains disposable frontend-inspection artifacts.

- [ ] **Step 4: Run full verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/walletQuota-pyc python3 -m compileall -q walletquota atg_pool wallet_quota.py atg_pool_report.py tests
python3 -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: compilation exits `0`, all tests pass, `git diff --check` prints nothing, and status contains only intentional source/doc changes.

- [ ] **Step 5: Perform one real read-only smoke test**

Run `python3 wallet_quota.py --verbose` with the existing local `.env`. Confirm ranges total correctly, account count is shown, local JSON is valid, and no write endpoint is present in the client allowlist.

---

## Plan self-review

- All design requirements map to Tasks 1–5.
- Public types and method names are consistent across tasks.
- No task introduces a write endpoint or depends on a real API test.
- Full account values have exactly one output surface: local aggregate JSON.
