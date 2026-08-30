-- 20260830224810_add_provenance_columns.sql
-- Add text_provenance to sentences and word_provenance to words for durable translation provenance

ALTER TABLE sentences ADD COLUMN text_provenance TEXT;
ALTER TABLE words ADD COLUMN word_provenance TEXT;
