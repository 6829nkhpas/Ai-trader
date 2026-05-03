// ──────────────────────────────────────────────────────────────
// hasher.js — Argon2id Password Hashing with System-Wide PEPPER
//
// Security invariants:
//   1. PEPPER is loaded from AUTH_PEPPER env var at module init.
//      Process EXITS if missing or too short — the service MUST NOT
//      start without the pepper.
//   2. PEPPER is prepended to the plaintext password before hashing.
//      This ensures that even if the DB is fully compromised, passwords
//      cannot be brute-forced without the application-layer secret.
//   3. Each hash uses a unique 128-bit random salt (auto-generated
//      by the argon2 library). Two identical passwords will always
//      produce different hash strings.
//   4. Argon2id parameters follow OWASP 2024 server-side recommendations:
//      - 64 MiB memory cost (memory-hard)
//      - 3 iterations (time cost)
//      - 4 parallel lanes
//      - 256-bit output digest
// ──────────────────────────────────────────────────────────────

import argon2 from 'argon2';

// ── PEPPER Guard ────────────────────────────────────────────
const PEPPER = process.env.AUTH_PEPPER;

if (!PEPPER || PEPPER.length < 32) {
  console.error(
    'FATAL: AUTH_PEPPER env var is missing or too short (minimum 32 characters). ' +
    'The auth service cannot start without a valid pepper.'
  );
  process.exit(1);
}

// ── Argon2id Configuration (OWASP 2024) ────────────────────
const ARGON2_OPTIONS = {
  type:        argon2.argon2id,    // Argon2id — hybrid (side-channel + GPU resistant)
  memoryCost:  65536,              // 64 MiB
  timeCost:    3,                  // 3 iterations
  parallelism: 4,                  // 4 lanes
  hashLength:  32,                 // 256-bit digest
  saltLength:  16,                 // 128-bit random salt per hash
};

/**
 * Hash a plaintext password with Argon2id + system pepper.
 *
 * @param {string} plaintext — The user's raw password
 * @returns {Promise<string>} — Argon2id encoded hash string
 *          Format: $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
 *
 * The pepper is prepended to the password before hashing:
 *   actualInput = PEPPER + plaintext
 * The salt is auto-generated (unique per call).
 */
export async function hashPassword(plaintext) {
  const peppered = PEPPER + plaintext;
  return argon2.hash(peppered, ARGON2_OPTIONS);
}

/**
 * Verify a plaintext password against a stored Argon2id hash.
 *
 * @param {string} plaintext — The user's raw password attempt
 * @param {string} storedHash — The stored Argon2id encoded hash string
 * @returns {Promise<boolean>} — true if password matches, false otherwise
 *
 * The same pepper is prepended before verification:
 *   actualInput = PEPPER + plaintext
 * argon2.verify extracts the salt and parameters from the storedHash.
 */
export async function verifyPassword(plaintext, storedHash) {
  const peppered = PEPPER + plaintext;
  return argon2.verify(storedHash, peppered);
}
