BEGIN;

CREATE TABLE IF NOT EXISTS public.wallet_crawl_runs (
    run_id UUID PRIMARY KEY,
    source_sha256 TEXT NOT NULL UNIQUE CHECK (length(source_sha256) = 64),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL CHECK (finished_at >= started_at),
    status TEXT NOT NULL CHECK (status IN ('success', 'partial')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.wallet_channel_snapshots (
    run_id UUID NOT NULL REFERENCES public.wallet_crawl_runs (run_id),
    provider TEXT NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_]*$'),
    status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'error')),
    complete BOOLEAN NOT NULL,
    order_count BIGINT CHECK (order_count >= 0),
    order_amount NUMERIC,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(summary) = 'object'),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(metadata) = 'object'),
    error_category TEXT,
    error_message TEXT,
    PRIMARY KEY (run_id, provider),
    CHECK (status <> 'success' OR complete)
);

CREATE TABLE IF NOT EXISTS public.wallet_orders (
    run_id UUID NOT NULL,
    provider TEXT NOT NULL,
    order_id TEXT NOT NULL CHECK (order_id <> ''),
    amount NUMERIC NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
    income NUMERIC,
    balance NUMERIC,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(attributes) = 'object'),
    PRIMARY KEY (run_id, provider, order_id),
    FOREIGN KEY (run_id, provider)
        REFERENCES public.wallet_channel_snapshots (run_id, provider)
);

CREATE TABLE IF NOT EXISTS public.wallet_accounts (
    run_id UUID NOT NULL,
    provider TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'bound_upi', 'bank_account', 'order_payee_upi', 'order_payee_bank'
    )),
    account_id TEXT NOT NULL CHECK (account_id <> ''),
    order_id TEXT NOT NULL DEFAULT '',
    account TEXT,
    upi TEXT,
    bank_name TEXT,
    ifsc TEXT,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(attributes) = 'object'),
    PRIMARY KEY (run_id, provider, kind, account_id, order_id),
    FOREIGN KEY (run_id, provider)
        REFERENCES public.wallet_channel_snapshots (run_id, provider),
    CHECK (kind NOT IN ('bound_upi', 'order_payee_upi')
        OR (upi IS NOT NULL AND upi <> '')),
    CHECK (kind NOT IN ('bank_account', 'order_payee_bank')
        OR (account IS NOT NULL AND account <> ''))
);

CREATE INDEX IF NOT EXISTS wallet_crawl_runs_finished_at_idx
    ON public.wallet_crawl_runs (finished_at DESC);
CREATE INDEX IF NOT EXISTS wallet_channel_snapshots_provider_run_idx
    ON public.wallet_channel_snapshots (provider, run_id);
CREATE INDEX IF NOT EXISTS wallet_orders_provider_order_id_idx
    ON public.wallet_orders (provider, order_id);
CREATE INDEX IF NOT EXISTS wallet_accounts_provider_kind_idx
    ON public.wallet_accounts (provider, kind);

CREATE OR REPLACE VIEW public.wallet_run_totals AS
WITH channel_counts AS (
    SELECT
        run_id,
        count(*) FILTER (WHERE status = 'success') AS channel_success,
        count(*) FILTER (WHERE status = 'partial') AS channel_partial,
        count(*) FILTER (WHERE status = 'error') AS channel_error
    FROM public.wallet_channel_snapshots
    GROUP BY run_id
), trusted_order_totals AS (
    SELECT
        orders.run_id,
        count(*) AS order_count,
        sum(orders.amount) AS order_amount
    FROM public.wallet_orders AS orders
    JOIN public.wallet_channel_snapshots AS channels
        ON channels.run_id = orders.run_id
        AND channels.provider = orders.provider
    WHERE channels.status = 'success' AND channels.complete
    GROUP BY orders.run_id
)
SELECT
    runs.run_id,
    runs.started_at,
    runs.finished_at,
    runs.status,
    coalesce(counts.channel_success, 0::bigint) AS channel_success,
    coalesce(counts.channel_partial, 0::bigint) AS channel_partial,
    coalesce(counts.channel_error, 0::bigint) AS channel_error,
    coalesce(totals.order_count, 0::bigint) AS order_count,
    coalesce(totals.order_amount, 0::numeric) AS order_amount
FROM public.wallet_crawl_runs AS runs
LEFT JOIN channel_counts AS counts ON counts.run_id = runs.run_id
LEFT JOIN trusted_order_totals AS totals ON totals.run_id = runs.run_id;

COMMIT;
