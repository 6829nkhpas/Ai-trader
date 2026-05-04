// ──────────────────────────────────────────────────────────────
// repository/user_profile.repository.js — Data access layer for User Profiles
// Implements transparent application-level AES-256 encryption for PII fields.
// ──────────────────────────────────────────────────────────────

import { encryptSymmetric, decryptSymmetric } from '../crypto/encryption.js';

/**
 * Encrypts sensitive fields if they exist.
 */
function encryptProfileData(data) {
  return {
    ...data,
    legalName: data.legalName ? encryptSymmetric(data.legalName) : null,
    panNumber: data.panNumber ? encryptSymmetric(data.panNumber) : null,
    residentialAddress: data.residentialAddress ? encryptSymmetric(data.residentialAddress) : null,
  };
}

/**
 * Decrypts sensitive fields if they exist.
 */
function decryptProfileData(row) {
  if (!row) return null;
  return {
    ...row,
    legal_name: row.legal_name ? decryptSymmetric(row.legal_name) : null,
    pan_number: row.pan_number ? decryptSymmetric(row.pan_number) : null,
    residential_address: row.residential_address ? decryptSymmetric(row.residential_address) : null,
  };
}

/**
 * Insert or Update a user profile with encrypted PII.
 * @param {import('pg').PoolClient} client
 * @param {{ userId: string, legalName?: string, panNumber?: string, residentialAddress?: string, aadhaarMetadata?: object, kycStatus?: string | null }} data
 * @returns {Promise<object>}
 */
export async function upsertUserProfile(client, data) {
  const encrypted = encryptProfileData(data);

  const result = await client.query(
    `INSERT INTO user_profiles (user_id, legal_name, pan_number, residential_address, aadhaar_metadata, kyc_status)
     VALUES ($1, $2, $3, $4, $5, COALESCE($6, 'PENDING'))
     ON CONFLICT (user_id) DO UPDATE SET
       legal_name = EXCLUDED.legal_name,
       pan_number = EXCLUDED.pan_number,
       residential_address = EXCLUDED.residential_address,
       aadhaar_metadata = EXCLUDED.aadhaar_metadata,
        kyc_status = COALESCE($6, user_profiles.kyc_status),
       updated_at = NOW()
     RETURNING id, user_id, legal_name, pan_number, residential_address, aadhaar_metadata, kyc_status, created_at, updated_at`,
    [
      data.userId,
      encrypted.legalName,
      encrypted.panNumber,
      encrypted.residentialAddress,
      data.aadhaarMetadata ? JSON.stringify(data.aadhaarMetadata) : null,
      data.kycStatus ?? null
    ]
  );

  // Return decrypted data back to the business layer
  return decryptProfileData(result.rows[0]);
}

/**
 * Find a user profile by user ID and decrypt PII.
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 * @returns {Promise<object | null>}
 */
export async function findUserProfileByUserId(client, userId) {
  const result = await client.query(
    `SELECT id, user_id, legal_name, pan_number, residential_address, aadhaar_metadata, kyc_status, created_at, updated_at
     FROM user_profiles
     WHERE user_id = $1`,
    [userId]
  );

  return decryptProfileData(result.rows[0]);
}

/**
 * Retrieves the raw ciphertext profile (used strictly for auditing/debugging).
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 * @returns {Promise<object | null>}
 */
export async function getRawUserProfileCiphertext(client, userId) {
  const result = await client.query(
    `SELECT id, user_id, legal_name, pan_number, residential_address, aadhaar_metadata, kyc_status, created_at, updated_at
     FROM user_profiles
     WHERE user_id = $1`,
    [userId]
  );

  return result.rows[0] || null;
}

/**
 * Updates the KYC status for a user profile.
 * @param {import('pg').PoolClient} client
 * @param {string} userId
 * @param {string} newStatus
 */
export async function updateKycStatus(client, userId, newStatus) {
  await client.query(
    `UPDATE user_profiles 
     SET kyc_status = $1, updated_at = NOW() 
     WHERE user_id = $2`,
    [newStatus, userId]
  );
}
