// ──────────────────────────────────────────────────────────────
// repository/mfa.repository.js — Data access layer for MFA
// Operations on the user_mfa_vault table.
// ──────────────────────────────────────────────────────────────

/**
 * Find the active MFA record for a user.
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 * @param {string} [mfaType='totp']
 * @returns {Promise<{id: string, secret_encrypted: string, is_active: boolean} | null>}
 */
export async function findMfaRecord(client, userId, mfaType = 'totp') {
  const result = await client.query(
    'SELECT id, secret_encrypted, is_active FROM user_mfa_vault WHERE user_id = $1 AND mfa_type = $2',
    [userId, mfaType]
  );
  return result.rows[0] || null;
}

/**
 * Upsert an MFA record for a user.
 * @param {import('pg').PoolClient} client
 * @param {{ userId: string, mfaType: string, secretEncrypted: string, isActive: boolean }} data
 */
export async function upsertMfaRecord(client, { userId, mfaType = 'totp', secretEncrypted, isActive = false }) {
  await client.query(
    `INSERT INTO user_mfa_vault (user_id, mfa_type, secret_encrypted, is_active)
     VALUES ($1, $2, $3, $4)
     ON CONFLICT (user_id, mfa_type) DO UPDATE
     SET secret_encrypted = EXCLUDED.secret_encrypted,
         is_active = EXCLUDED.is_active,
         updated_at = NOW()`,
    [userId, mfaType, secretEncrypted, isActive]
  );
}

/**
 * Mark an MFA record as active.
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 * @param {string} [mfaType='totp']
 */
export async function activateMfaRecord(client, userId, mfaType = 'totp') {
  await client.query(
    'UPDATE user_mfa_vault SET is_active = TRUE, updated_at = NOW() WHERE user_id = $1 AND mfa_type = $2',
    [userId, mfaType]
  );
}
