-- Applied by the pipeline on every run; safe to run more than once.

CREATE TABLE IF NOT EXISTS series (
    series_id    TEXT PRIMARY KEY,
    sgs_code     INTEGER NOT NULL UNIQUE,
    description  TEXT NOT NULL,
    unit         TEXT NOT NULL,
    frequency    TEXT NOT NULL CHECK (frequency IN ('business_daily', 'calendar_daily', 'monthly'))
);

-- One value per series per date: the primary key makes duplicates impossible.
CREATE TABLE IF NOT EXISTS observations (
    series_id   TEXT          NOT NULL REFERENCES series (series_id),
    ref_date    DATE          NOT NULL,
    value       NUMERIC(14,6) NOT NULL,
    loaded_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, ref_date)
);

-- Values the Central Bank changed after they were first loaded.
CREATE TABLE IF NOT EXISTS revisions (
    revision_id  BIGSERIAL PRIMARY KEY,
    series_id    TEXT          NOT NULL REFERENCES series (series_id),
    ref_date     DATE          NOT NULL,
    old_value    NUMERIC(14,6) NOT NULL,
    new_value    NUMERIC(14,6) NOT NULL,
    revised_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- One row per pipeline execution, with per-series counts in `details`.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id       BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    inserted     INTEGER NOT NULL DEFAULT 0,
    updated      INTEGER NOT NULL DEFAULT 0,
    rejected     INTEGER NOT NULL DEFAULT 0,
    details      JSONB   NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT
);
