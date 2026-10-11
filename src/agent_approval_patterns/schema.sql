-- Synthetic domain: orders that failed to release to a downstream "release service".

CREATE TABLE IF NOT EXISTS orders (
    id               text PRIMARY KEY,
    status           text NOT NULL,              -- CREATED | RELEASE_FAILED | RELEASED | ON_HOLD
    release_attempts int  NOT NULL DEFAULT 0,
    blocked          boolean NOT NULL DEFAULT false,  -- the release service rejects blocked orders
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- The durable record of an escalation. Approval works from this row, never from a paused run.
CREATE TABLE IF NOT EXISTS proposals (
    id                  bigserial PRIMARY KEY,
    agent               text  NOT NULL,
    order_id            text  NOT NULL REFERENCES orders(id),
    reason              text  NOT NULL,
    proposed_actions    jsonb NOT NULL,              -- the only actions a reviewer may pick
    context             jsonb NOT NULL DEFAULT '{}',
    source_run_id       text  NOT NULL,              -- the agent run that escalated
    status              text  NOT NULL DEFAULT 'PENDING',
    approved_by         text,
    approval_run_id     text,                        -- the run that holds the claim
    selected_action     text,
    execution_result    jsonb,
    verification_result jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    status_updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Every action an agent took or would have taken (audit mode), with the outcome.
CREATE TABLE IF NOT EXISTS action_log (
    id         bigserial PRIMARY KEY,
    run_id     text NOT NULL,
    agent      text NOT NULL,
    order_id   text NOT NULL,
    action     text NOT NULL,
    mode       text NOT NULL,                       -- executed | dry_run | not_implemented | failed
    detail     jsonb NOT NULL DEFAULT '{}',
    outcome    text,                                -- verification outcome, for real calls only
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE action_log ADD COLUMN IF NOT EXISTS outcome text;

-- A minimal Postgres-backed task queue (FOR UPDATE SKIP LOCKED).
CREATE TABLE IF NOT EXISTS tasks (
    id           bigserial PRIMARY KEY,
    kind         text  NOT NULL,
    payload      jsonb NOT NULL,
    status       text  NOT NULL DEFAULT 'QUEUED',   -- QUEUED | RUNNING | DONE | FAILED
    attempts     int   NOT NULL DEFAULT 0,
    max_attempts int   NOT NULL DEFAULT 3,
    locked_until timestamptz,
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- The mock release service's own ledger, so tests can count downstream calls and effects.
CREATE TABLE IF NOT EXISTS release_calls (
    id              bigserial PRIMARY KEY,
    order_id        text NOT NULL,
    idempotency_key text,
    applied         boolean NOT NULL,               -- false when deduplicated or rejected
    outcome         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
