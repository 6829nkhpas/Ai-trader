// ──────────────────────────────────────────────────────────────
// routes/webhook.routes.js — Webhook Endpoints
// ──────────────────────────────────────────────────────────────

import { handleKycVendorWebhook } from '../controllers/webhook.controller.js';
import { handlePolarWebhook } from '../controllers/billing.webhook.controller.js';

export function registerWebhookRoutes(app) {
  // Webhooks are typically called by external vendors, hence no authGuard
  // In production, this would be guarded by signature verification
  app.post('/api/webhooks/kyc/vendor-status', handleKycVendorWebhook);

  // Polar Billing Webhooks
  app.post('/api/webhooks/polar', handlePolarWebhook);
}
