-- 20260907042302_overview_selections.sql
-- Dedicated storage for Overview tab (sentence_index = 0) word selections

CREATE TABLE IF NOT EXISTS overview_selections (
    session_zid TEXT NOT NULL,
    token_order INTEGER NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_zid, token_order),
    FOREIGN KEY (session_zid) REFERENCES sessions(zid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_overview_selections_session ON overview_selections(session_zid);
