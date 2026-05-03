// ──────────────────────────────────────────────────────────────
// routes/kyc.routes.js — KYC Endpoints
// ──────────────────────────────────────────────────────────────

import { handleVerifyPan, handleLivenessCheck, handleGetUploadUrl } from '../controllers/kyc.controller.js';
import { authGuard } from '../middleware/auth.guard.js';

export function registerKycRoutes(app) {
  app.post('/api/kyc/pan/verify', { preHandler: [authGuard] }, handleVerifyPan);
  app.post('/api/kyc/liveness', { preHandler: [authGuard] }, handleLivenessCheck);
  app.get('/api/kyc/upload-url', { preHandler: [authGuard] }, handleGetUploadUrl);
}
