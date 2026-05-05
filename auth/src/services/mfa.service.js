// ──────────────────────────────────────────────────────────────
// services/mfa.service.js — TOTP MFA logic
// Handles generation, QR code rendering, and verification.
// ──────────────────────────────────────────────────────────────

import { generateSecret, generateURI, verify } from 'otplib';
import QRCode from 'qrcode';
import { findMfaRecord, upsertMfaRecord, activateMfaRecord } from '../repository/mfa.repository.js';
import { encryptSymmetric, decryptSymmetric } from '../crypto/encryption.js';
import { AuthenticationError } from '../errors/index.js';

/**
 * Generate a new TOTP secret for a user and return the QR code data URL.
 * 
 * @param {import('pg').Pool} pool
 * @param {{ id: string, email: string }} user
 * @returns {Promise<{ qrCodeDataURL: string, manualSecret: string }>}
 */
export async function generateMfa(pool, user) {
  // Generate a secret
  const secret = generateSecret();

  // Create otpauth URL
  // Format: otpauth://totp/Issuer:Email?secret=...&issuer=Issuer
  const otpauth = generateURI({
    issuer: 'AI-Trader',
    label: user.email,
    secret
  });

  // Generate QR code data URL
  const qrCodeDataURL = await QRCode.toDataURL(otpauth);

  // Encrypt the secret for storage
  const encryptedSecret = encryptSymmetric(secret);

  // Store it as inactive
  await upsertMfaRecord(pool, {
    userId: user.id,
    mfaType: 'totp',
    secretEncrypted: encryptedSecret,
    isActive: false
  });

  return {
    qrCodeDataURL,
    manualSecret: secret
  };
}

/**
 * Verify a TOTP token against the user's stored secret.
 * 
 * @param {import('pg').Pool} pool
 * @param {string} userId
 * @param {string} token
 * @returns {Promise<boolean>}
 * @throws {AuthenticationError}
 */
export async function verifyMfa(pool, userId, token) {
  if (!token) {
    throw new AuthenticationError('MFA token is required.');
  }

  const record = await findMfaRecord(pool, userId, 'totp');

  if (!record) {
    throw new AuthenticationError('MFA is not configured for this user.');
  }

  // Decrypt the secret
  let secret;
  try {
    secret = decryptSymmetric(record.secret_encrypted);
  } catch (err) {
    throw new Error('Failed to decrypt MFA secret. System configuration may be invalid.');
  }

  // Verify the token
  // (In otplib, verify often returns boolean, but keeping existing logic structure)
  const isValid = await verify({ token, secret });

  if (!isValid) {
    throw new AuthenticationError('Invalid MFA token.');
  }

  // If it was inactive, mark it as active
  if (!record.is_active) {
    await activateMfaRecord(pool, userId, 'totp');
  }

  return true;
}

/**
 * Returns MFA status for a user.
 *
 * @param {import('pg').Pool} pool
 * @param {string} userId
 * @returns {Promise<{ exists: boolean, isActive: boolean }>}
 */
export async function getMfaStatus(pool, userId) {
  const record = await findMfaRecord(pool, userId, 'totp');
  return {
    exists: !!record,
    isActive: record?.is_active === true,
  };
}
