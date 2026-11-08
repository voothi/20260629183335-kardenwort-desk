CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE TABLE IF NOT EXISTS preference_metadata (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_view_state (
    session_zid TEXT NOT NULL REFERENCES sessions(zid) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    active_card INTEGER NOT NULL DEFAULT 1 CHECK(active_card > 0),
    active_tokens TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    touched_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY(session_zid, client_id)
);
CREATE INDEX IF NOT EXISTS idx_client_view_expiration ON client_view_state(touched_at);
CREATE TABLE IF NOT EXISTS browser_state_imports (
    client_id TEXT PRIMARY KEY,
    completed_at INTEGER NOT NULL DEFAULT (unixepoch())
);
