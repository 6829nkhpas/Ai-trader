// ──────────────────────────────────────────────────────────────
// services/oauth.service.js — Google OAuth 2.0 logic
// Handles ID token verification and automatic user registration.
// ──────────────────────────────────────────────────────────────

import { OAuth2Client } from 'google-auth-library';
import { config } from '../config.js';
import { findUserByEmail, insertUser } from '../repository/user.repository.js';
import { AuthenticationError } from '../errors/index.js';

// Initialize OAuth2 client lazily
let oauthClient = null;

function getClient() {
  if (!oauthClient) {
    oauthClient = new OAuth2Client(config.googleClientId);
  }
  return oauthClient;
}

/**
 * Verifies a Google ID token and returns the user identity.
 * Automatically registers the user if they don't exist.
 * 
 * @param {import('pg').Pool} pool
 * @param {string} idToken
 * @returns {Promise<{ id: string, email: string, role: string }>}
 * @throws {AuthenticationError}
 */
export async function loginWithGoogle(pool, idToken) {
  if (!idToken) {
    throw new AuthenticationError('Google ID token is required.');
  }

  let payload;
  try {
    const client = getClient();
    // Verify token against our client ID
    // If config.googleClientId is not set, we can skip audience check for development,
    // but in production we must check audience.
    const verifyOpts = { idToken };
    if (config.googleClientId) {
      verifyOpts.audience = config.googleClientId;
    }

    const ticket = await client.verifyIdToken(verifyOpts);
    payload = ticket.getPayload();
  } catch (err) {
    console.error('[OAUTH] Google token verification failed:', err.message);
    throw new AuthenticationError('Invalid Google ID token.');
  }

  const email = payload.email.toLowerCase();
  const displayName = payload.name || null;
  const emailVerified = payload.email_verified;

  if (!emailVerified) {
    throw new AuthenticationError('Google account email must be verified.');
  }

  try {
    let user = await findUserByEmail(pool, email);

    if (!user) {
      // Auto-register
      console.log(`[AUTH] Auto-registering Google user: ${email}`);
      const newUser = await insertUser(pool, { email, displayName, role: 'user' });

      // Note: We don't insert a password credential for OAuth users yet.
      // If we expand user_credentials, we could insert an 'oauth_google' credential.
      user = newUser;
    }

    // Role or display_name might be needed for token issuance/frontend
    if (!user.role || user.displayName === undefined) {
      const fullUserResult = await pool.users.findUnique({ 
        where: { id: user.id }, 
        select: { id: true, email: true, role: true, display_name: true } 
      });
      user = {
        ...fullUserResult,
        displayName: fullUserResult.display_name
      };
    }

    return {
      id: user.id,
      email: user.email,
      role: user.role,
      displayName: user.displayName,
    };
  } catch (err) {
    throw err;
  }
}
