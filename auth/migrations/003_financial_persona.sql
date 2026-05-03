-- ============================================================
-- FINANCIAL PERSONA — Phase 5 Schema
-- PII Siloing, AES-256 Field Encryption, and Audit Trails
-- ============================================================

-- ──────────────────────────────────────────────────────────────
-- 1. audit_logs — Phase 4 Audit Trail
--    Captures all row-level modifications for compliance
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name  VARCHAR(128) NOT NULL,
    record_id   UUID NOT NULL,
    action      VARCHAR(16)  NOT NULL, -- INSERT, UPDATE, DELETE
    old_data    JSONB,
    new_data    JSONB,
    changed_by  UUID,                  -- Can be NULL if system action
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────────
-- 2. user_profiles — Phase 5 Financial Persona
--    Decoupled 1:1 from users. Stores only ciphertext for PII.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID UNIQUE NOT NULL
                        REFERENCES users(id) ON DELETE CASCADE,
    legal_name          TEXT,  -- AES-256-GCM ciphertext
    pan_number          TEXT,  -- AES-256-GCM ciphertext
    residential_address TEXT,  -- AES-256-GCM ciphertext
    aadhaar_metadata    JSONB, -- Context only (no raw PII)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup by user_id
CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles (user_id);

-- ──────────────────────────────────────────────────────────────
-- 3. Audit Triggers for user_profiles
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION audit_user_profiles_changes()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        INSERT INTO audit_logs (table_name, record_id, action, old_data, changed_by)
        VALUES ('user_profiles', OLD.id, 'DELETE', row_to_json(OLD)::jsonb, OLD.user_id);
        RETURN OLD;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO audit_logs (table_name, record_id, action, old_data, new_data, changed_by)
        VALUES ('user_profiles', NEW.id, 'UPDATE', row_to_json(OLD)::jsonb, row_to_json(NEW)::jsonb, NEW.user_id);
        RETURN NEW;
    ELSIF (TG_OP = 'INSERT') THEN
        INSERT INTO audit_logs (table_name, record_id, action, new_data, changed_by)
        VALUES ('user_profiles', NEW.id, 'INSERT', row_to_json(NEW)::jsonb, NEW.user_id);
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_user_profiles ON user_profiles;
CREATE TRIGGER trg_audit_user_profiles
    AFTER INSERT OR UPDATE OR DELETE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION audit_user_profiles_changes();

-- Auto-update trigger for updated_at (reusing function from 001)
DROP TRIGGER IF EXISTS trg_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER trg_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
