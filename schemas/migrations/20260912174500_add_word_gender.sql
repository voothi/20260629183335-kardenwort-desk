-- 20260912174500_add_word_gender.sql
-- Add gender column to words table for grammatical noun gender persistence

ALTER TABLE words ADD COLUMN gender TEXT;
