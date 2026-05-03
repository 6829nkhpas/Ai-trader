-- ============================================================
-- IDENTITY VAULT — Phase 1 Auth Schema
-- Argon2id-optimized, UUID v4 primary keys, MFA-ready
-- Run order: auto-executed by PostgreSQL on first container boot
--            via docker-entrypoint-initdb.d volume mount
-- ============================================================

-- Enable pgcrypto for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ──────────────────────────────────────────────────────────────
-- 1. users — Core identity record
-- ──────────────────────────────────────────────────────────────
CREATE TABLE users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             VARCHAR(320) NOT NULL,            -- RFC 5321 max email length
    display_name      VARCHAR(128),
    role              VARCHAR(32)  NOT NULL DEFAULT 'user',  -- 'user' only for Phase 1
    email_verified_at TIMESTAMPTZ,                      -- NULL = unverified (Phase 2+)
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_users_email UNIQUE (email)
);

-- Fast lookup for login-by-email
CREATE INDEX idx_users_email ON users (email);

-- ──────────────────────────────────────────────────────────────
-- 2. user_credentials — Argon2id password hash storage
--    Separated from users for future credential-type expansion
--    (OAuth tokens, passkeys, API keys, etc.)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE user_credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID         NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
    credential_type VARCHAR(32)  NOT NULL DEFAULT 'password',
    password_hash   VARCHAR(512) NOT NULL,              -- Argon2id: ~97 chars typical; 512 headroom
    salt_metadata   VARCHAR(128),                       -- Audit trail only (not used in verification)
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- One credential per type per user (prevents duplicate password rows)
    CONSTRAINT uq_user_credentials_user_type UNIQUE (user_id, credential_type)
);

-- ──────────────────────────────────────────────────────────────
-- 3. user_mfa_vault — TOTP / WebAuthn secrets (Phase 2+)
--    Schema laid now; activation deferred to MFA subphases
-- ──────────────────────────────────────────────────────────────
CREATE TABLE user_mfa_vault (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID         NOT NULL
                     REFERENCES users(id) ON DELETE CASCADE,
    mfa_type         VARCHAR(32)  NOT NULL DEFAULT 'totp',   -- 'totp', 'webauthn'
    secret_encrypted TEXT         NOT NULL,                   -- AES-256-GCM encrypted TOTP seed
    is_active        BOOLEAN      NOT NULL DEFAULT FALSE,
    backup_codes     TEXT[],                                  -- Hashed backup recovery codes
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- One MFA method per type per user
    CONSTRAINT uq_user_mfa_user_type UNIQUE (user_id, mfa_type)
);

-- ──────────────────────────────────────────────────────────────
-- Auto-update trigger for updated_at columns
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_user_credentials_updated_at
    BEFORE UPDATE ON user_credentials
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_user_mfa_vault_updated_at
    BEFORE UPDATE ON user_mfa_vault
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
