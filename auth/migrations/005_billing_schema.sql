-- ============================================================
-- BILLING SCHEMA — Phase 6.1
-- Polar Subscription Tracking and PCI Compliance Guardrails
-- ============================================================

-- ──────────────────────────────────────────────────────────────
-- 1. Alter users table to track Polar customer
-- ──────────────────────────────────────────────────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS polar_customer_id VARCHAR(255) UNIQUE;

-- ──────────────────────────────────────────────────────────────
-- 2. subscriptions table
--    PCI Compliant: Only stores references to Polar
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    polar_sub_id       VARCHAR(255) UNIQUE NOT NULL,
    plan_tier          VARCHAR(32) NOT NULL, -- 'Weekly', 'Monthly', 'Yearly'
    current_period_end TIMESTAMPTZ NOT NULL,
    status             VARCHAR(32) NOT NULL,
    proration_metadata JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup by user_id
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions (user_id);

-- ──────────────────────────────────────────────────────────────
-- Auto-update trigger for updated_at
-- ──────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_subscriptions_updated_at ON subscriptions;
CREATE TRIGGER trg_subscriptions_updated_at
    BEFORE UPDATE ON subscriptions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
