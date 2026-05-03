// ──────────────────────────────────────────────────────────────
// services/auth.service.js — Business logic layer
// Orchestrates validation, hashing, and DB operations.
// Called by controllers — never touches HTTP request/response.
// ──────────────────────────────────────────────────────────────

import { hashPassword } from '../crypto/hasher.js';
import { config } from '../config.js';
import { PasswordComplexityError, DuplicateEmailError } from '../errors/index.js';
import { findUserByEmail, insertUser, insertCredential } from '../repository/user.repository.js';

// ── Password Complexity Validator ───────────────────────────

/**
 * Validates password against configured complexity rules.
 * @param {string} password — Plaintext password
 * @throws {PasswordComplexityError}
 */
export function validatePasswordComplexity(password) {
  const rules = config.password;

  if (typeof password !== 'string') {
    throw new PasswordComplexityError('Password must be a string.');
  }

  if (password.length < rules.minLength) {
    throw new PasswordComplexityError(
      `Minimum length is ${rules.minLength} characters (got ${password.length}).`
    );
  }

  if (password.length > rules.maxLength) {
    throw new PasswordComplexityError(
      `Maximum length is ${rules.maxLength} characters (prevents DoS).`
    );
  }

  if (rules.requireUppercase && !/[A-Z]/.test(password)) {
    throw new PasswordComplexityError('Must contain at least one uppercase letter.');
  }

  if (rules.requireLowercase && !/[a-z]/.test(password)) {
    throw new PasswordComplexityError('Must contain at least one lowercase letter.');
  }

  if (rules.requireDigit && !/[0-9]/.test(password)) {
    throw new PasswordComplexityError('Must contain at least one digit.');
  }

  if (rules.requireSpecial && !/[!@#$%^&*()_+\-=\[\]{};':",./<>?\\|`~]/.test(password)) {
    throw new PasswordComplexityError(
      'Must contain at least one special character (!@#$%^&*()_+-=[]{};\':\",./<>?).'
    );
  }
}

// ── Registration Service ────────────────────────────────────

/**
 * Transaction-safe user registration.
 *
 * @param {import('pg').Pool} pool — PostgreSQL connection pool
 * @param {Object} params
 * @param {string} params.email
 * @param {string} params.password
 * @param {string} [params.displayName]
 * @returns {Promise<{id: string, email: string, role: string, created_at: string}>}
 * @throws {PasswordComplexityError}
 * @throws {DuplicateEmailError}
 */
export async function registerUser(pool, { email, password, displayName }) {
  // 1. Validate password complexity
  validatePasswordComplexity(password);

  // 2. Normalize email
  const normalizedEmail = email.trim().toLowerCase();
  if (!normalizedEmail || !normalizedEmail.includes('@')) {
    throw new PasswordComplexityError('Invalid email format.');
  }

  // 3. Begin transaction
  const client = await pool.connect();

  try {
    await client.query('BEGIN');

    // 4. Advisory duplicate check (constraint is the real guard)
    const existing = await findUserByEmail(client, normalizedEmail);
    if (existing) {
      throw new DuplicateEmailError(normalizedEmail);
    }

    // 5. Insert user
    const user = await insertUser(client, {
      email: normalizedEmail,
      displayName: displayName || null,
    });

    // 6. Hash password with Argon2id + PEPPER
    const passwordHash = await hashPassword(password);

    // 7. Insert credential
    await insertCredential(client, { userId: user.id, passwordHash });

    // 8. Commit
    await client.query('COMMIT');

    console.log(`[AUTH] User registered: ${user.email} (${user.id})`);
    return {
      id:         user.id,
      email:      user.email,
      role:       user.role,
      created_at: user.created_at,
    };

  } catch (err) {
    await client.query('ROLLBACK');

    // Race condition: unique constraint violation on email
    if (err.code === '23505' && err.constraint === 'uq_users_email') {
      throw new DuplicateEmailError(normalizedEmail);
    }
    throw err;
  } finally {
    client.release();
  }
}
