// ──────────────────────────────────────────────────────────────
// repository/token.repository.js — Data access for refresh tokens
// All raw SQL queries against the refresh_tokens table live here.
// ──────────────────────────────────────────────────────────────

/**
 * Store a new refresh token (hashed).
 * @param {import('pg').PoolClient} client
 * @param {{ userId: string, tokenHash: string, familyId: string, expiresAt: Date }} data
 * @returns {Promise<{ id: string, created_at: string }>}
 */
export async function insertRefreshToken(client, { userId, tokenHash, familyId, expiresAt }) {
  const result = await client.query(
    `INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at)
     VALUES ($1, $2, $3, $4)
     RETURNING id, created_at`,
    [userId, tokenHash, familyId, expiresAt]
  );
  return result.rows[0];
}

/**
 * Find a refresh token record by its SHA-256 hash.
 * @param {import('pg').PoolClient} client
 * @param {string} tokenHash
 * @returns {Promise<{ id: string, user_id: string, token_hash: string, family_id: string, is_revoked: boolean, expires_at: string, created_at: string } | null>}
 */
export async function findRefreshTokenByHash(client, tokenHash) {
  const result = await client.query(
    `SELECT id, user_id, token_hash, family_id, is_revoked, expires_at, created_at
     FROM refresh_tokens
     WHERE token_hash = $1`,
    [tokenHash]
  );
  return result.rows[0] || null;
}

/**
 * Revoke a single refresh token by ID.
 * @param {import('pg').PoolClient} client
 * @param {string} tokenId
 */
export async function revokeRefreshToken(client, tokenId) {
  await client.query(
    `UPDATE refresh_tokens SET is_revoked = true WHERE id = $1`,
    [tokenId]
  );
}

/**
 * Revoke ALL tokens in a rotation family (breach response).
 * @param {import('pg').PoolClient} client
 * @param {string} familyId
 */
export async function revokeAllTokensByFamily(client, familyId) {
  await client.query(
    `UPDATE refresh_tokens SET is_revoked = true WHERE family_id = $1`,
    [familyId]
  );
}

/**
 * Revoke ALL refresh tokens for a user (full session wipe).
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 */
export async function revokeAllTokensByUser(client, userId) {
  await client.query(
    `UPDATE refresh_tokens SET is_revoked = true WHERE user_id = $1`,
    [userId]
  );
}

/**
 * Get all active (non-revoked, non-expired) token IDs for a user.
 * Used for blacklisting all active JTIs during breach wipe.
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 * @returns {Promise<Array<{ id: string, family_id: string, created_at: string }>>}
 */
export async function getActiveTokensByUser(client, userId) {
  const result = await client.query(
    `SELECT id, family_id, created_at
     FROM refresh_tokens
     WHERE user_id = $1 AND is_revoked = false AND expires_at > now()`,
    [userId]
  );
  return result.rows;
}

/**
 * Delete expired tokens (housekeeping).
 * @param {import('pg').PoolClient} client
 */
export async function deleteExpiredTokens(client) {
  const result = await client.query(
    `DELETE FROM refresh_tokens WHERE expires_at < now()`
  );
  return result.rowCount;
}
