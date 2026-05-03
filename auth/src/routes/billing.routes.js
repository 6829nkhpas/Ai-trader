// ──────────────────────────────────────────────────────────────
// billing.routes.js
// Exposes the billing controller endpoints.
// ──────────────────────────────────────────────────────────────

import { billingController } from '../controllers/billing.controller.js';
import { authGuard } from '../middleware/auth.guard.js';

export function registerBillingRoutes(app) {
  // Public route to view plans
  app.get('/api/billing/plans', billingController.getPlans);

  // Authenticated route to initiate checkout
  // PreHandler ensures request.user is populated by JWT
  app.post('/api/billing/checkout', { preHandler: [authGuard] }, billingController.createCheckout.bind(billingController));

  // Authenticated route to transition plans (Upgrade/Downgrade)
  app.post('/api/billing/transition', { preHandler: [authGuard] }, billingController.transitionSubscription.bind(billingController));
}
