// ──────────────────────────────────────────────────────────────
// routes/auth.routes.js — Route definitions
// Registers all auth-related HTTP routes on the Fastify instance.
// Separates route wiring from controller logic.
// ──────────────────────────────────────────────────────────────

import { handleRegister, handleHealth } from '../controllers/auth.controller.js';

/**
 * Register all auth routes on the Fastify app instance.
 * @param {import('fastify').FastifyInstance} app
 */
export function registerAuthRoutes(app) {
  app.post('/api/auth/register', handleRegister);
  app.get('/api/auth/health', handleHealth);
}
