CREATE TABLE IF NOT EXISTS provider_cooldowns (
    provider_key TEXT PRIMARY KEY,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_provider_state (
    session_zid TEXT NOT NULL,
    field TEXT NOT NULL CHECK(field IN ('active_text_provider', 'active_lemma_provider', 'active_provider')),
    value TEXT NOT NULL,
    PRIMARY KEY(session_zid, field)
);
