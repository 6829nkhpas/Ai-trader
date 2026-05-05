/**
 * Find the active MFA record for a user.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 * @param {string} [mfaType='totp']
 */
export async function findMfaRecord(client, userId, mfaType = 'totp') {
  const mfa = await client.user_mfa_vault.findFirst({
    where: { user_id: userId, mfa_type: mfaType },
    select: { id: true, secret_encrypted: true, is_active: true }
  });
  return mfa;
}

/**
 * Upsert an MFA record for a user.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {{ userId: string, mfaType: string, secretEncrypted: string, isActive: boolean }} data
 */
export async function upsertMfaRecord(client, { userId, mfaType = 'totp', secretEncrypted, isActive = false }) {
  await client.user_mfa_vault.upsert({
    where: {
      user_id_mfa_type: {
        user_id: userId,
        mfa_type: mfaType
      }
    },
    update: {
      secret_encrypted: secretEncrypted,
      is_active: isActive,
      updated_at: new Date()
    },
    create: {
      user_id: userId,
      mfa_type: mfaType,
      secret_encrypted: secretEncrypted,
      is_active: isActive
    }
  });
}

/**
 * Mark an MFA record as active.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 * @param {string} [mfaType='totp']
 */
export async function activateMfaRecord(client, userId, mfaType = 'totp') {
  await client.user_mfa_vault.update({
    where: {
      user_id_mfa_type: {
        user_id: userId,
        mfa_type: mfaType
      }
    },
    data: {
      is_active: true,
      updated_at: new Date()
    }
  });
}
