DELETE FROM session_provider_state
WHERE session_zid NOT IN (SELECT zid FROM sessions WHERE deleted_at IS NULL);
