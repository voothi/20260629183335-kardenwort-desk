-- 003_deduplicate_words_and_unique_index.sql
-- Clean up existing duplicate word rows and enforce uniqueness

DELETE FROM words WHERE id NOT IN (
    SELECT MIN(id) FROM words GROUP BY session_zid, sentence_index, token_order, quotation, lemma
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_words_unique_token ON words(session_zid, sentence_index, token_order, quotation, lemma);
