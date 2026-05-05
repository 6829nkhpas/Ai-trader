/**
 * Store a new refresh token (hashed).
 * @param {import('@prisma/client').PrismaClient} client
 * @param {{ userId: string, tokenHash: string, familyId: string, expiresAt: Date }} data
 */
export async function insertRefreshToken(client, { userId, tokenHash, familyId, expiresAt }) {
  const token = await client.refresh_tokens.create({
    data: {
      user_id: userId,
      token_hash: tokenHash,
      family_id: familyId,
      expires_at: expiresAt
    },
    select: { id: true, created_at: true }
  });
  return token;
}

/**
 * Find a refresh token record by its SHA-256 hash.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} tokenHash
 */
export async function findRefreshTokenByHash(client, tokenHash) {
  const token = await client.refresh_tokens.findFirst({
    where: { token_hash: tokenHash },
    select: { id: true, user_id: true, token_hash: true, family_id: true, is_revoked: true, expires_at: true, created_at: true }
  });
  return token;
}

/**
 * Revoke a single refresh token by ID.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} tokenId
 */
export async function revokeRefreshToken(client, tokenId) {
  await client.refresh_tokens.update({
    where: { id: tokenId },
    data: { is_revoked: true }
  });
}

/**
 * Revoke ALL tokens in a rotation family (breach response).
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} familyId
 */
export async function revokeAllTokensByFamily(client, familyId) {
  await client.refresh_tokens.updateMany({
    where: { family_id: familyId },
    data: { is_revoked: true }
  });
}

/**
 * Revoke ALL refresh tokens for a user (full session wipe).
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 */
export async function revokeAllTokensByUser(client, userId) {
  await client.refresh_tokens.updateMany({
    where: { user_id: userId },
    data: { is_revoked: true }
  });
}

/**
 * Get all active (non-revoked, non-expired) token IDs for a user.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 */
export async function getActiveTokensByUser(client, userId) {
  const tokens = await client.refresh_tokens.findMany({
    where: {
      user_id: userId,
      is_revoked: false,
      expires_at: { gt: new Date() }
    },
    select: { id: true, family_id: true, created_at: true }
  });
  return tokens;
}

/**
 * Delete expired tokens (housekeeping).
 * @param {import('@prisma/client').PrismaClient} client
 */
export async function deleteExpiredTokens(client) {
  const result = await client.refresh_tokens.deleteMany({
    where: {
      expires_at: { lt: new Date() }
    }
  });
  return result.count;
}
