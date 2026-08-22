-- 002_projects_and_soft_delete.sql
-- Migration adding deleted_at soft-deletion timestamp and hierarchical project tables

-- 1. Soft-delete support for sessions
ALTER TABLE sessions ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL;

-- 2. Hierarchical Projects Table
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER DEFAULT NULL,
    title TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT DEFAULT '',
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP DEFAULT NULL,
    FOREIGN KEY (parent_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- 3. Junction table linking sessions to projects with explicit ordering
CREATE TABLE IF NOT EXISTS project_sessions (
    project_id INTEGER NOT NULL,
    session_zid TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, session_zid),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (session_zid) REFERENCES sessions(zid) ON DELETE CASCADE
);

-- 4. Indexes for fast hierarchy resolution and filtering
CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON sessions(deleted_at);
CREATE INDEX IF NOT EXISTS idx_projects_parent ON projects(parent_id);
CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug);
CREATE INDEX IF NOT EXISTS idx_projects_deleted ON projects(deleted_at);
CREATE INDEX IF NOT EXISTS idx_project_sessions_project ON project_sessions(project_id, order_index);
CREATE INDEX IF NOT EXISTS idx_project_sessions_session ON project_sessions(session_zid);
