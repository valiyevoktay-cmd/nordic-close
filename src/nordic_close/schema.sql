-- Nordic close: SQLite schema.
-- Money is stored as integer minor units (all four currencies use 2 decimals).
-- Exchange rates are stored as exact decimal text.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS entity (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    currency        TEXT NOT NULL CHECK (length(currency) = 3),
    country         TEXT NOT NULL,
    is_parent       INTEGER NOT NULL DEFAULT 0 CHECK (is_parent IN (0, 1)),
    ownership_pct   INTEGER NOT NULL DEFAULT 100 CHECK (ownership_pct BETWEEN 1 AND 100)
);

CREATE TABLE IF NOT EXISTS group_account (
    code             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE')),
    statement        TEXT NOT NULL CHECK (statement IN ('BS', 'PL')),
    report_line      TEXT NOT NULL,
    sort_order       INTEGER NOT NULL,
    is_intercompany  INTEGER NOT NULL DEFAULT 0 CHECK (is_intercompany IN (0, 1))
);

CREATE TABLE IF NOT EXISTS account_map (
    entity_code  TEXT NOT NULL REFERENCES entity (code),
    local_code   TEXT NOT NULL,
    local_name   TEXT NOT NULL,
    group_code   TEXT NOT NULL REFERENCES group_account (code),
    PRIMARY KEY (entity_code, local_code)
);

CREATE TABLE IF NOT EXISTS journal (
    journal_id    TEXT PRIMARY KEY,
    entity_code   TEXT NOT NULL REFERENCES entity (code),
    booking_date  TEXT NOT NULL CHECK (booking_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    period        TEXT NOT NULL CHECK (period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    description   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_journal_entity_period ON journal (entity_code, period);

CREATE TABLE IF NOT EXISTS journal_line (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_id    TEXT NOT NULL REFERENCES journal (journal_id),
    line_no       INTEGER NOT NULL,
    local_code    TEXT NOT NULL,
    debit_minor   INTEGER NOT NULL DEFAULT 0 CHECK (debit_minor >= 0),
    credit_minor  INTEGER NOT NULL DEFAULT 0 CHECK (credit_minor >= 0),
    ref           TEXT NOT NULL DEFAULT '',
    memo          TEXT NOT NULL DEFAULT '',
    ic_partner    TEXT REFERENCES entity (code),
    ic_eur_minor  INTEGER,
    CHECK (NOT (debit_minor > 0 AND credit_minor > 0)),
    UNIQUE (journal_id, line_no)
);
CREATE INDEX IF NOT EXISTS ix_line_journal ON journal_line (journal_id);

CREATE TABLE IF NOT EXISTS fx_rate (
    rate_date  TEXT NOT NULL,
    currency   TEXT NOT NULL,
    per_eur    TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (rate_date, currency)
);

CREATE TABLE IF NOT EXISTS budget_line (
    entity_code   TEXT NOT NULL REFERENCES entity (code),
    period        TEXT NOT NULL,
    group_code    TEXT NOT NULL REFERENCES group_account (code),
    amount_minor  INTEGER NOT NULL,
    PRIMARY KEY (entity_code, period, group_code)
);

CREATE TABLE IF NOT EXISTS bank_statement_line (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_code     TEXT NOT NULL REFERENCES entity (code),
    statement_date  TEXT NOT NULL,
    ref             TEXT NOT NULL DEFAULT '',
    amount_minor    INTEGER NOT NULL,
    description     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_bank_entity_date ON bank_statement_line (entity_code, statement_date);

CREATE TABLE IF NOT EXISTS bank_statement_balance (
    entity_code    TEXT NOT NULL REFERENCES entity (code),
    period         TEXT NOT NULL,
    opening_minor  INTEGER NOT NULL,
    closing_minor  INTEGER NOT NULL,
    preparer       TEXT NOT NULL DEFAULT '',
    reviewer       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entity_code, period)
);

CREATE TABLE IF NOT EXISTS explanation (
    period    TEXT NOT NULL,
    line_key  TEXT NOT NULL,
    text      TEXT NOT NULL,
    author    TEXT NOT NULL DEFAULT '',
    reviewer  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (period, line_key)
);

CREATE TABLE IF NOT EXISTS period_lock (
    period     TEXT PRIMARY KEY,
    locked_at  TEXT NOT NULL,
    locked_by  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS close_run (
    run_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    period                 TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN ('ready', 'blocked')),
    input_hash             TEXT NOT NULL,
    output_hash            TEXT NOT NULL,
    presentation_currency  TEXT NOT NULL,
    started_at             TEXT NOT NULL,
    summary_json           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_run_period ON close_run (period);

CREATE TABLE IF NOT EXISTS close_run_gate (
    run_id       INTEGER NOT NULL REFERENCES close_run (run_id),
    gate         TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('pass', 'fail')),
    reason       TEXT NOT NULL DEFAULT '',
    detail_json  TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, gate)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    action  TEXT NOT NULL,
    period  TEXT,
    actor   TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT ''
);
