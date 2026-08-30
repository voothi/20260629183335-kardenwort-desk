-- 20260830224810_add_worker_status_columns.sql
-- Add worker liveness and lifecycle tracking columns to sessions table

ALTER TABLE sessions ADD COLUMN worker_status TEXT;
ALTER TABLE sessions ADD COLUMN worker_started_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN worker_heartbeat_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN worker_finished_at TIMESTAMP;
