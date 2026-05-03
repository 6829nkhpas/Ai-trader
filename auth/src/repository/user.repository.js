// ──────────────────────────────────────────────────────────────
// repository/user.repository.js — Data access layer for users
// All raw SQL queries against users / user_credentials live here.
// Controllers and services NEVER write SQL directly.
// ──────────────────────────────────────────────────────────────

/**
 * Find a user by email.
 * @param {import('pg').PoolClient} client — PG client (within transaction)
 * @param {string} email — Normalized email
 * @returns {Promise<{id: string, email: string} | null>}
 */
export async function findUserByEmail(client, email) {
  const result = await client.query(
    'SELECT id, email FROM users WHERE email = $1',
    [email]
  );
  return result.rows[0] || null;
}

/**
 * Insert a new user record.
 * @param {import('pg').PoolClient} client
 * @param {{ email: string, displayName: string | null, role: string }} data
 * @returns {Promise<{id: string, email: string, role: string, created_at: string}>}
 */
export async function insertUser(client, { email, displayName, role = 'user' }) {
  const result = await client.query(
    `INSERT INTO users (email, display_name, role)
     VALUES ($1, $2, $3)
     RETURNING id, email, role, created_at`,
    [email, displayName, role]
  );
  return result.rows[0];
}

/**
 * Insert a password credential for a user.
 * @param {import('pg').PoolClient} client
 * @param {{ userId: string, passwordHash: string }} data
 */
export async function insertCredential(client, { userId, passwordHash }) {
  await client.query(
    `INSERT INTO user_credentials (user_id, credential_type, password_hash)
     VALUES ($1, 'password', $2)`,
    [userId, passwordHash]
  );
}

/**
 * Fetch the password hash for a user by email.
 * Used for login verification (Phase 2+).
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 * @returns {Promise<string | null>} — The stored Argon2id hash, or null
 */
export async function getPasswordHash(client, userId) {
  const result = await client.query(
    `SELECT password_hash FROM user_credentials
     WHERE user_id = $1 AND credential_type = 'password'`,
    [userId]
  );
  return result.rows[0]?.password_hash || null;
}
