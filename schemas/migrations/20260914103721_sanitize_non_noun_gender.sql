-- 20260914103721_sanitize_non_noun_gender.sql
-- Sanitize existing non-noun gender noise in words table

UPDATE words SET gender = NULL WHERE LOWER(gender) IN ('none', 'null', 'n/a', '-') OR gender = 'None' OR gender = '';
