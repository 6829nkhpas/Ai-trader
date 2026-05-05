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
} from '../repository/token.repository.js';
import { blacklistJti } from '../middleware/blacklist.js';

function hashToken(token) {
  return crypto.createHash('sha256').update(token).digest('hex');
}

export async function issueTokenPair(prisma, user, mfaVerified = false) {
  const rawRefreshToken = uuidv4();
  const tokenHash = hashToken(rawRefreshToken);
  const familyId = uuidv4();
  const expiresAt = new Date(Date.now() + config.jwt.refreshTtl * 1000);

  const { token: accessToken, jti: accessTokenJti } = signAccessToken({
    sub: user.id,
    email: user.email,
    role: user.role,
    mfa_verified: mfaVerified
  });

  await insertRefreshToken(prisma, {
    userId: user.id,
    tokenHash,
    familyId,
    expiresAt,
  });

  return { accessToken, refreshToken: rawRefreshToken, accessTokenJti };
}

export async function rotateRefreshToken(prisma, oldRefreshToken) {
if (!oldRefreshToken) throw new AuthenticationError('No refresh token provided.');

  const tokenHash = hashToken(oldRefreshToken);

  return await prisma.$transaction(async (tx) => {
    const storedToken = await findRefreshTokenByHash(tx, tokenHash);

    if (!storedToken) {
      throw new AuthenticationError('Invalid refresh token.');
    }

    if (storedToken.is_revoked) {
      console.warn(`[AUTH] BREACH DETECTED: Token reuse attempt for user ${storedToken.user_id}`);

      await revokeAllTokensByFamily(tx, storedToken.family_id);
      await revokeAllTokensByUser(tx, storedToken.user_id);

      throw new TokenReuseError('Token reuse detected. All sessions terminated.');
    }

    if (new Date() > new Date(storedToken.expires_at)) {
      throw new AuthenticationError('Refresh token expired.');
    }

    await revokeRefreshToken(tx, storedToken.id);

    const user = await tx.users.findUnique({
      where: { id: storedToken.user_id }
    });
    if (!user) {
      throw new AuthenticationError('User no longer exists.');
    }

    const newRawRefreshToken = uuidv4();
    const newTokenHash = hashToken(newRawRefreshToken);
    const expiresAt = new Date(Date.now() + config.jwt.refreshTtl * 1000);

    const { token: accessToken } = signAccessToken({
      sub: user.id,
      email: user.email,
      role: user.role,
      mfa_verified: true
    });

    await insertRefreshToken(tx, {
      userId: user.id,
      tokenHash: newTokenHash,
      familyId: storedToken.family_id,
      expiresAt,
    });

    return { accessToken, refreshToken: newRawRefreshToken };
  });
}

export async function revokeSession(prisma, refreshToken, accessTokenJti) {
  if (accessTokenJti) {
    await blacklistJti(accessTokenJti, config.jwt.accessTtl);
  }

  if (refreshToken) {
    const tokenHash = hashToken(refreshToken);
    const storedToken = await findRefreshTokenByHash(prisma, tokenHash);
    if (storedToken) {
      await revokeRefreshToken(prisma, storedToken.id);
    }
  }
}

export async function revokeAllUserSessions(prisma, userId) {
  await revokeAllTokensByUser(prisma, userId);
}
