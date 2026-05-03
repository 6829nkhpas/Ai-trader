// ──────────────────────────────────────────────────────────────
// controllers/auth.controller.js — HTTP request handlers
// Thin layer: validates input shape, delegates to services,
// maps errors to HTTP status codes. No business logic here.
// ──────────────────────────────────────────────────────────────

import { registerUser } from '../services/auth.service.js';
import { getPool } from '../db.js';
import { PasswordComplexityError, DuplicateEmailError } from '../errors/index.js';

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
