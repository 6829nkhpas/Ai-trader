// ──────────────────────────────────────────────────────────────
// routes/auth.routes.js — Route definitions
// Registers all auth-related HTTP routes on the Fastify instance.
// Separates route wiring from controller logic.
// ──────────────────────────────────────────────────────────────

import { handleRegister, handleHealth, handleLogin, handleRefresh, handleLogout, handleGoogleLogin, handleGenerateMfa, handleVerifyMfa, handleSession } from '../controllers/auth.controller.js';
import { authGuard } from '../middleware/auth.guard.js';

/**
 * Register all auth routes on the Fastify app instance.
 * @param {import('fastify').FastifyInstance} app
 */
export function registerAuthRoutes(app) {
  app.post('/api/auth/register', handleRegister);
  app.post('/api/auth/login', handleLogin);
  app.post('/api/auth/refresh', handleRefresh);
  app.post('/api/auth/logout', { preHandler: [authGuard] }, handleLogout);
  app.get('/api/auth/session', { preHandler: [authGuard] }, handleSession);
  app.get('/api/auth/health', handleHealth);

  // Password Reset endpoints (To be implemented)
  // app.post('/api/auth/password/reset-request', handleRequestPasswordReset);
  // app.post('/api/auth/password/reset', handleResetPassword);

  // Phase 3 endpoints
  app.post('/api/auth/oauth/google', handleGoogleLogin);
  app.post('/api/auth/mfa/generate', { preHandler: [authGuard] }, handleGenerateMfa);
  app.post('/api/auth/mfa/verify', { preHandler: [authGuard] }, handleVerifyMfa);
}
