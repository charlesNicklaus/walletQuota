-- Apply to the Sulu Pay PostgreSQL database before importing the first capture.
BEGIN;

CREATE TABLE IF NOT EXISTS public.t_payout_pool_snapshot (
    run_id UUID NOT NULL,
    run_started_at TIMESTAMPTZ NOT NULL,
    provider TEXT NOT NULL,
    channel TEXT,
    observed_at TIMESTAMPTZ,
    order_types TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'error')),
    complete BOOLEAN NOT NULL CHECK (complete = (status = 'success')),
    order_count BIGINT CHECK (order_count >= 0),
    order_amount_inr NUMERIC CHECK (order_amount_inr >= 0),
    PRIMARY KEY (run_id, provider),
    CHECK (
        (status = 'error' AND order_count IS NULL AND order_amount_inr IS NULL)
        OR (status <> 'error' AND order_count IS NOT NULL AND order_amount_inr IS NOT NULL)
    ),
    CHECK (order_types IS NULL OR order_types ~ '^[0-9]+(,[0-9]+)*$')
);

CREATE INDEX IF NOT EXISTS t_payout_pool_snapshot_latest_idx
    ON public.t_payout_pool_snapshot (run_started_at DESC, run_id DESC);

-- Every row belongs to the newest capture. A missing channel cannot inherit
-- an older capture's result. scope_empty applies only to the listed order_types.
CREATE OR REPLACE VIEW public.v_payout_pool_current AS
WITH providers(provider) AS (
    VALUES ('atgpay'), ('millerpay'), ('penguinpay'), ('sharkpay'),
           ('smartwallet'), ('showpay'), ('hoyopay')
), latest AS (
    SELECT run_id, run_started_at
    FROM public.t_payout_pool_snapshot
    ORDER BY run_started_at DESC, run_id DESC
    LIMIT 1
)
SELECT p.provider, s.channel, s.observed_at, s.order_types, s.status, s.complete,
       s.order_count, s.order_amount_inr,
       CASE
           WHEN NULLIF(s.channel, '') IS NULL OR s.order_types IS NULL
               OR s.observed_at IS NULL OR s.status = 'error'
               OR s.observed_at < statement_timestamp() - interval '30 minutes'
               OR s.observed_at > statement_timestamp()
               OR latest.run_started_at > statement_timestamp() THEN 'unknown'
           WHEN s.order_count > 0 THEN 'nonempty'
           WHEN s.status = 'success' AND s.complete AND s.order_count = 0 THEN 'scope_empty'
           ELSE 'unknown'
       END AS pool_state,
       latest.run_id, latest.run_started_at
FROM providers p
LEFT JOIN latest ON true
LEFT JOIN public.t_payout_pool_snapshot s
    ON s.run_id = latest.run_id AND s.provider = p.provider
ORDER BY p.provider;

COMMIT;
