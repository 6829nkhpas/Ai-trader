// ──────────────────────────────────────────────────────────────
// services/token.service.js — Token lifecycle orchestration
// Handles JWT issuance, stateful refresh token rotation,
// breach detection, and session revocation.
// ──────────────────────────────────────────────────────────────

import crypto from 'node:crypto';
import { v4 as uuidv4 } from 'uuid';
import { signAccessToken } from '../crypto/jwt.provider.js';
import { config } from '../config.js';
import { AuthenticationError, TokenReuseError } from '../errors/index.js';
import {
  insertRefreshToken,
  findRefreshTokenByHash,
  revokeRefreshToken,
  revokeAllTokensByFamily,
  revokeAllTokensByUser,
  getActiveTokensByUser
} from '../repository/token.repository.js';
import { blacklistJti } from '../middleware/blacklist.js';
import { findUserById } from '../repository/user.repository.js';

/**
 * SHA-256 hashes a token for storage/lookup.
 * @param {string} token 
 * @returns {string} hex hash
 */
function hashToken(token) {
  return crypto.createHash('sha256').update(token).digest('hex');
}

/**
 * Issue a fresh access and refresh token pair.
 * 
 * @param {import('pg').Pool} pool 
 * @param {{ id: string, email: string, role: string }} user 
 * @returns {Promise<{ accessToken: string, refreshToken: string, accessTokenJti: string }>}
 */
export async function issueTokenPair(pool, user) {
  const client = await pool.connect();
  try {
    const rawRefreshToken = uuidv4();
    const tokenHash = hashToken(rawRefreshToken);
    const familyId = uuidv4();
    const expiresAt = new Date(Date.now() + config.jwt.refreshTtl * 1000);

    // Sign stateless access token
    const { token: accessToken, jti: accessTokenJti } = signAccessToken({
      sub: user.id,
      email: user.email,
      role: user.role,
    });

    // Store stateful refresh token
    await insertRefreshToken(client, {
      userId: user.id,
      tokenHash,
      familyId,
      expiresAt,
    });

    return { accessToken, refreshToken: rawRefreshToken, accessTokenJti };
  } finally {
    client.release();
  }
}

/**
 * Rotate a refresh token: issue new pair, revoke old.
 * Detects token reuse and triggers breach wipe.
 * 
 * @param {import('pg').Pool} pool 
 * @param {string} oldRefreshToken 
 * @returns {Promise<{ accessToken: string, refreshToken: string }>}
 * @throws {AuthenticationError} If token invalid or expired
 * @throws {TokenReuseError} If breach detected (token already revoked)
 */
export async function rotateRefreshToken(pool, oldRefreshToken) {
  if (!oldRefreshToken) throw new AuthenticationError('No refresh token provided.');

  const tokenHash = hashToken(oldRefreshToken);
  const client = await pool.connect();

  try {
    await client.query('BEGIN');

    const storedToken = await findRefreshTokenByHash(client, tokenHash);
    
    // Not found
    if (!storedToken) {
      await client.query('ROLLBACK');
      throw new AuthenticationError('Invalid refresh token.');
    }

    // BREACH DETECTED: Token was already used/revoked
    if (storedToken.is_revoked) {
      console.warn(`[AUTH] BREACH DETECTED: Token reuse attempt for user ${storedToken.user_id}`);
      
      // 1. Revoke the entire refresh family
      await revokeAllTokensByFamily(client, storedToken.family_id);
      
      // 2. Revoke ALL sessions for this user (safest response to credential theft)
      await revokeAllTokensByUser(client, storedToken.user_id);
      
      // 3. Blacklist all active JTIs in Redis
      const activeTokens = await getActiveTokensByUser(client, storedToken.user_id);
      // Note: We don't have the JTIs stored in DB to blacklist them all instantly, 
      // but revoking all refresh tokens forces re-login within 15 mins.
      // In a stricter system, we'd store JTIs or user epoch to reject all access tokens instantly.

      await client.query('COMMIT');
      throw new TokenReuseError('Token reuse detected. All sessions terminated.');
    }

    // Expired
    if (new Date() > new Date(storedToken.expires_at)) {
      await client.query('ROLLBACK');
      throw new AuthenticationError('Refresh token expired.');
    }

    // Valid: revoke old token
    await revokeRefreshToken(client, storedToken.id);

    // Fetch user details for new access token
    const user = await findUserById(client, storedToken.user_id);
    if (!user) {
      await client.query('ROLLBACK');
      throw new AuthenticationError('User no longer exists.');
    }

    // Issue new pair within the SAME family
    const newRawRefreshToken = uuidv4();
    const newTokenHash = hashToken(newRawRefreshToken);
    const expiresAt = new Date(Date.now() + config.jwt.refreshTtl * 1000);

    const { token: accessToken } = signAccessToken({
      sub: user.id,
      email: user.email,
      role: user.role,
    });

    await insertRefreshToken(client, {
      userId: user.id,
      tokenHash: newTokenHash,
      familyId: storedToken.family_id, // maintain family tree
      expiresAt,
    });

    await client.query('COMMIT');

    return { accessToken, refreshToken: newRawRefreshToken };

  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

/**
 * Revoke a specific session (Logout).
 * 
 * @param {import('pg').Pool} pool 
 * @param {string} refreshToken 
 * @param {string} accessTokenJti 
 */
export async function revokeSession(pool, refreshToken, accessTokenJti) {
  const client = await pool.connect();
  try {
    // 1. Blacklist the access token JTI instantly
    if (accessTokenJti) {
      await blacklistJti(accessTokenJti, config.jwt.accessTtl);
    }

    // 2. Revoke the refresh token
    if (refreshToken) {
      const tokenHash = hashToken(refreshToken);
      const storedToken = await findRefreshTokenByHash(client, tokenHash);
      if (storedToken) {
        await revokeRefreshToken(client, storedToken.id);
      }
    }
  } finally {
    client.release();
  }
}

/**
 * Force revoke all sessions for a user.
 * 
 * @param {import('pg').Pool} pool 
 * @param {string} userId 
 */
export async function revokeAllUserSessions(pool, userId) {
  const client = await pool.connect();
  try {
    await revokeAllTokensByUser(client, userId);
  } finally {
    client.release();
  }
}
