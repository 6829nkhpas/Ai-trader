// ──────────────────────────────────────────────────────────────
// routes/kyc.routes.js — KYC Endpoints
// ──────────────────────────────────────────────────────────────

import { handleVerifyPan, handleLivenessCheck, handleGetUploadUrl, handleUpsertProfile, handleGetProfile } from '../controllers/kyc.controller.js';
import { authGuard } from '../middleware/auth.guard.js';
import { mfaGuard } from '../middleware/mfa.guard.js';

export function registerKycRoutes(app) {
  app.post('/api/kyc/pan/verify', { preHandler: [authGuard, mfaGuard] }, handleVerifyPan);
  app.post('/api/kyc/liveness', { preHandler: [authGuard, mfaGuard] }, handleLivenessCheck);
  app.get('/api/kyc/upload-url', { preHandler: [authGuard, mfaGuard] }, handleGetUploadUrl);
  app.post('/api/kyc/profile', { preHandler: [authGuard, mfaGuard] }, handleUpsertProfile);
  app.get('/api/kyc/profile', { preHandler: [authGuard, mfaGuard] }, handleGetProfile);
}
