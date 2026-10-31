-- Track who changed what, for compliance reviews
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_entity ON audit_log(entity, entity_id);
