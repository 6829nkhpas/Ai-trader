// ──────────────────────────────────────────────────────────────
// crypto/encryption.js — Symmetric encryption for MFA seeds
// Uses AES-256-GCM authenticated encryption.
// The key is derived from the AUTH_PEPPER environment variable.
// ──────────────────────────────────────────────────────────────

import crypto from 'node:crypto';
import { config } from '../config.js';

const ALGORITHM = 'aes-256-gcm';
const IV_LENGTH = 16;
const SALT_LENGTH = 16;
const TAG_LENGTH = 16;

/**
 * Derives a 32-byte key from the pepper.
 * @returns {Buffer}
 */
function getKey() {
  if (!config.authPepper || config.authPepper.length < 32) {
    throw new Error('AUTH_PEPPER must be at least 32 characters for encryption.');
  }
  return crypto.createHash('sha256').update(config.authPepper).digest();
}

/**
 * Encrypts a plaintext string.
 * @param {string} plaintext
 * @returns {string} Base64 formatted string containing IV, Auth Tag, and Ciphertext.
 */
export function encryptSymmetric(plaintext) {
  const iv = crypto.randomBytes(IV_LENGTH);
  const key = getKey();
  
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);
  
  let encrypted = cipher.update(plaintext, 'utf8', 'hex');
  encrypted += cipher.final('hex');
  
  const authTag = cipher.getAuthTag();
  
  // Format: iv:authTag:encrypted (all in hex, joined by colon)
  return `${iv.toString('hex')}:${authTag.toString('hex')}:${encrypted}`;
}

/**
 * Decrypts a ciphertext string formatted as iv:authTag:encrypted.
 * @param {string} encryptedPayload
 * @returns {string} The decrypted plaintext.
 */
export function decryptSymmetric(encryptedPayload) {
  const parts = encryptedPayload.split(':');
  if (parts.length !== 3) {
    throw new Error('Invalid encrypted payload format.');
  }
  
  const iv = Buffer.from(parts[0], 'hex');
  const authTag = Buffer.from(parts[1], 'hex');
  const encryptedText = parts[2];
  
  const key = getKey();
  
  const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);
  decipher.setAuthTag(authTag);
  
  let decrypted = decipher.update(encryptedText, 'hex', 'utf8');
  decrypted += decipher.final('utf8');
  
  return decrypted;
}
