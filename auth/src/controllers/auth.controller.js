// ──────────────────────────────────────────────────────────────
// controllers/auth.controller.js — HTTP request handlers
// Thin layer: validates input shape, delegates to services,
// maps errors to HTTP status codes. No business logic here.
// ──────────────────────────────────────────────────────────────

import { registerUser, loginUser } from '../services/auth.service.js';
import { issueTokenPair, rotateRefreshToken, revokeSession } from '../services/token.service.js';
import { getPool } from '../db.js';
import { PasswordComplexityError, DuplicateEmailError, AuthenticationError } from '../errors/index.js';
import { config } from '../config.js';

const COOKIE_OPTS = {
  httpOnly: true,
  secure: process.env.NODE_ENV === 'production',
  sameSite: 'strict',
  path: '/api/auth',
  maxAge: config.jwt.refreshTtl,
};

/**
 * POST /api/auth/register
 */
export async function handleRegister(request, reply) {
  const { email, password, displayName } = request.body || {};

  if (!email || !password) {
    return reply.status(400).send({ error: 'email and password are required.' });
  }

  try {
    const user = await registerUser(getPool(), { email, password, displayName });
    return reply.status(201).send({ ok: true, user });
  } catch (err) {
    if (err instanceof PasswordComplexityError) {
      return reply.status(err.statusCode).send({ error: err.message });
    }
    if (err instanceof DuplicateEmailError) {
      return reply.status(err.statusCode).send({ error: err.message });
    }
    request.log.error(err);
    return reply.status(500).send({ error: 'Internal server error.' });
  }
}

/**
 * GET /api/auth/health
 */
export async function handleHealth() {
  return { status: 'ok', service: 'ai-trade-auth' };
}

/**
 * POST /api/auth/login
 */
export async function handleLogin(request, reply) {
  const { email, password } = request.body || {};

  try {
    const user = await loginUser(getPool(), { email, password });
    const { accessToken, refreshToken } = await issueTokenPair(getPool(), user);

    reply.setCookie('refresh_token', refreshToken, COOKIE_OPTS);
    return reply.status(200).send({ ok: true, accessToken, user });
  } catch (err) {
    if (err instanceof AuthenticationError) {
      return reply.status(err.statusCode).send({ error: err.message });
    }
    request.log.error(err);
    return reply.status(500).send({ error: 'Internal server error.' });
  }
}

/**
 * POST /api/auth/refresh
 */
export async function handleRefresh(request, reply) {
  const oldRefreshToken = request.cookies?.refresh_token;

  try {
    const { accessToken, refreshToken } = await rotateRefreshToken(getPool(), oldRefreshToken);
    
    reply.setCookie('refresh_token', refreshToken, COOKIE_OPTS);
    return reply.status(200).send({ ok: true, accessToken });
  } catch (err) {
    if (err.statusCode) {
      // Clear cookie on auth/reuse error
      reply.clearCookie('refresh_token', { path: '/api/auth' });
      return reply.status(err.statusCode).send({ error: err.message });
    }
    request.log.error(err);
    return reply.status(500).send({ error: 'Internal server error.' });
  }
}

/**
 * POST /api/auth/logout
 */
export async function handleLogout(request, reply) {
  const refreshToken = request.cookies?.refresh_token;
  const accessTokenJti = request.user?.jti;

  try {
    await revokeSession(getPool(), refreshToken, accessTokenJti);
    reply.clearCookie('refresh_token', { path: '/api/auth' });
    return reply.status(200).send({ ok: true });
  } catch (err) {
    request.log.error(err);
    return reply.status(500).send({ error: 'Internal server error.' });
  }
}
