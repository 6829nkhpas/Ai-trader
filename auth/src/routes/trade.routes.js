// ──────────────────────────────────────────────────────────────
// routes/trade.routes.js — Mock Trading Routes
// ──────────────────────────────────────────────────────────────

import { authGuard } from '../middleware/auth.guard.js';
import { requireVerified } from '../middleware/kyc.guard.js';
import { requireActiveSubscription } from '../middleware/billing.guard.js';

export function registerTradeRoutes(app) {
  // Mock endpoint to prove the KYC Global Lock enforcement
  // In a real microservice, requireVerified and requireActiveSubscription would be exported to an API Gateway or imported in orders-service.
  app.post('/api/v1/orders', { preHandler: [authGuard, requireVerified, requireActiveSubscription] }, async (req, reply) => {
    return reply.status(201).send({ message: 'Order executed successfully.' });
  });
}
