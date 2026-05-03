// ──────────────────────────────────────────────────────────────
// billing.controller.js
// Handles HTTP requests for billing, plans, and checkouts.
// ──────────────────────────────────────────────────────────────

import { billingService } from '../services/billing.service.js';

export class BillingController {
  /**
   * GET /billing/plans
   * Returns the product catalog mapping Polar price_ids to internal plan tiers.
   */
  async getPlans(request, reply) {
    try {
      const plans = billingService.getPlans();
      return reply.send({
        status: 'success',
        data: plans
      });
    } catch (error) {
      request.log.error(error);
      return reply.status(500).send({
        status: 'error',
        message: 'Failed to retrieve plans'
      });
    }
  }

  /**
   * POST /billing/checkout
   * Initiates a checkout session via Polar API for a given price_id.
   * Body requires: { priceId: string }
   */
  async createCheckout(request, reply) {
    try {
      const { priceId } = request.body;
      const userId = request.user.sub; // From JWT Auth Guard

      if (!priceId) {
        return reply.status(400).send({
          status: 'error',
          message: 'priceId is required'
        });
      }

      const session = await billingService.createCheckoutSession(userId, priceId);

      return reply.send({
        status: 'success',
        data: {
          checkoutUrl: session.checkoutUrl
        }
      });
    } catch (error) {
      request.log.error(error);
      if (error.message.includes('Conflict')) {
        return reply.status(409).send({
          status: 'error',
          message: error.message
        });
      }
      if (error.message.includes('Invalid Price ID')) {
        return reply.status(400).send({
          status: 'error',
          message: error.message
        });
      }
      return reply.status(500).send({
        status: 'error',
        message: 'Failed to create checkout session'
      });
    }
  }
}

export const billingController = new BillingController();
