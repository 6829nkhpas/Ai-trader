-- ──────────────────────────────────────────────────────────────
-- 002_refresh_tokens.sql — Refresh token storage for rotation
-- Supports family-based breach detection and per-user session wipe.
-- ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS refresh_tokens (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash    TEXT NOT NULL,
  family_id     UUID NOT NULL,
  is_revoked    BOOLEAN NOT NULL DEFAULT false,
  expires_at    TIMESTAMPTZ NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast lookup by hashed token value (login/refresh flow)
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_token_hash ON refresh_tokens(token_hash);

-- Family-based revocation (breach detection wipe)
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family_id ON refresh_tokens(family_id);

-- Per-user session listing / wipe
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
