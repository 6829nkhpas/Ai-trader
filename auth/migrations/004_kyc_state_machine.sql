-- ============================================================
-- KYC STATE MACHINE — Phase 5 Subphase 16
-- ============================================================

-- Add kyc_status to track onboarding state machine
ALTER TABLE user_profiles 
ADD COLUMN IF NOT EXISTS kyc_status VARCHAR(32) NOT NULL DEFAULT 'PENDING';

-- The existing `trg_audit_user_profiles` will automatically track changes 
-- to this column in `audit_logs` since it audits `AFTER UPDATE ON user_profiles`.
