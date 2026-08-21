-- 20260820173633_initial_schema.sql
-- Baseline relational schema for Kardenwort-Desk SQLite engine

CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    zid TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    source_language TEXT NOT NULL,
    target_language TEXT NOT NULL DEFAULT '',
    text_mode TEXT NOT NULL DEFAULT 'single',
    source_raw_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sentences (
    session_zid TEXT NOT NULL,
    sentence_index INTEGER NOT NULL,
    sentence_source TEXT NOT NULL,
    sentence_destination TEXT,
    sentence_destination2 TEXT,
    sentence_source_ipa TEXT,
    sentence_source_audio TEXT,
    PRIMARY KEY (session_zid, sentence_index),
    FOREIGN KEY (session_zid) REFERENCES sessions(zid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_zid TEXT NOT NULL,
    sentence_index INTEGER NOT NULL,
    token_order INTEGER NOT NULL,
    quotation TEXT NOT NULL COLLATE NOCASE,
    inflected_form TEXT COLLATE NOCASE,
    lemma TEXT NOT NULL COLLATE NOCASE,
    pos TEXT,
    morphology TEXT,
    ipa TEXT,
    word_destination TEXT,
    word_destination_inflected TEXT,
    selected INTEGER NOT NULL DEFAULT 0,
    leitner_box INTEGER DEFAULT 1,
    leitner_due TIMESTAMP,
    deck TEXT,
    classification_oxford TEXT,
    classification_goethe TEXT,
    extra_fields TEXT,
    FOREIGN KEY (session_zid, sentence_index) REFERENCES sentences(session_zid, sentence_index) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_words_session_sentence ON words(session_zid, sentence_index);
CREATE INDEX IF NOT EXISTS idx_words_lemma ON words(lemma);
CREATE INDEX IF NOT EXISTS idx_words_quotation ON words(quotation);
CREATE INDEX IF NOT EXISTS idx_words_inflected_form ON words(inflected_form);
CREATE INDEX IF NOT EXISTS idx_sentences_session ON sentences(session_zid);
CREATE INDEX IF NOT EXISTS idx_sessions_slug_lang ON sessions(slug, source_language, created_at);

