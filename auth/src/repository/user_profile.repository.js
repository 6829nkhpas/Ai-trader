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
    legalName: row.legal_name ? decryptSymmetric(row.legal_name) : null,
    panNumber: row.pan_number ? decryptSymmetric(row.pan_number) : null,
    residentialAddress: row.residential_address ? decryptSymmetric(row.residential_address) : null,
  };
}

/**
 * Insert or Update a user profile with encrypted PII.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {{ userId: string, legalName?: string, panNumber?: string, residentialAddress?: string, aadhaarMetadata?: object, kycStatus?: string | null }} data
 */
export async function upsertUserProfile(client, data) {
  const encrypted = encryptProfileData(data);

  const profile = await client.user_profiles.upsert({
    where: { user_id: data.userId },
    update: {
      legal_name: encrypted.legalName,
      pan_number: encrypted.panNumber,
      residential_address: encrypted.residentialAddress,
      aadhaar_metadata: data.aadhaarMetadata ? data.aadhaarMetadata : null,
      kyc_status: data.kycStatus ?? undefined,
      updated_at: new Date()
    },
    create: {
      user_id: data.userId,
      legal_name: encrypted.legalName,
      pan_number: encrypted.panNumber,
      residential_address: encrypted.residentialAddress,
      aadhaar_metadata: data.aadhaarMetadata ? data.aadhaarMetadata : null,
      kyc_status: data.kycStatus ?? 'PENDING'
    }
  });

  return decryptProfileData(profile);
}

/**
 * Find a user profile by user ID and decrypt PII.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 */
export async function findUserProfileByUserId(client, userId) {
  const profile = await client.user_profiles.findUnique({
    where: { user_id: userId }
  });
  return decryptProfileData(profile);
}

/**
 * Retrieves the raw ciphertext profile (used strictly for auditing/debugging).
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 */
export async function getRawUserProfileCiphertext(client, userId) {
  const profile = await client.user_profiles.findUnique({
    where: { user_id: userId }
  });
  return profile;
}

/**
 * Updates the KYC status for a user profile.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 * @param {string} newStatus
 */
export async function updateKycStatus(client, userId, newStatus) {
  await client.user_profiles.update({
    where: { user_id: userId },
    data: {
      kyc_status: newStatus,
      updated_at: new Date()
    }
  });
}
