// ──────────────────────────────────────────────────────────────
// billing.service.js — Core Billing & Subscription Logic
// Integrates with Polar API to manage customers and checkouts.
// ──────────────────────────────────────────────────────────────

import { config } from '../config.js';
import { billingRepository } from '../repository/billing.repository.js';
import { getPool } from '../db.js';
import { findUserById } from '../repository/user.repository.js';
import { BILLING_CATALOG } from '../config/billing.catalog.js';

export class BillingService {
  /**
   * Returns the mapped product catalog.
   */
  getPlans() {
    return BILLING_CATALOG;
  }

  /**
   * Checks if a user has a Polar Customer ID; provisions one if not.
   * STRICT GUARDRAIL: Does not handle or store credit card data.
   */
  async getOrProvisionCustomer(userId) {
    let polarCustomerId = await billingRepository.getPolarCustomerId(userId);
    if (polarCustomerId) {
      return polarCustomerId;
    }

    const pool = getPool();
    const client = await pool.connect();
    
    try {
      const user = await findUserById(client, userId);
      if (!user) {
        throw new Error('User not found');
      }

      // Provision customer via Polar API
      const response = await fetch('https://api.polar.sh/api/v1/customers', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${config.polar.accessToken}`,
        },
        body: JSON.stringify({
          email: user.email,
          organization_id: config.polar.organizationId,
          metadata: {
            user_id: userId
          }
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(`Failed to provision Polar customer: ${JSON.stringify(errorData)}`);
      }

      const data = await response.json();
      polarCustomerId = data.id;

      // Persist the reference locally
      await billingRepository.setPolarCustomerId(userId, polarCustomerId);

      return polarCustomerId;
    } finally {
      client.release();
    }
  }

  /**
   * Creates a checkout session via Polar API
   */
  async createCheckoutSession(userId, priceId) {
    if (!BILLING_CATALOG[priceId]) {
      throw new Error('Invalid Price ID');
    }

    // 1. Ensure user has a Polar customer ID
    const polarCustomerId = await this.getOrProvisionCustomer(userId);

    // 2. Prevent duplicate active subscriptions
    const activeSubs = await billingRepository.getActiveSubscriptions(userId);
    const hasActiveForProduct = activeSubs.some(sub => sub.plan_tier === BILLING_CATALOG[priceId].tier);
    if (hasActiveForProduct) {
      throw new Error('Conflict: User already has an active subscription for this product');
    }

    // 3. Initiate checkout (Returns a URL to the Polar hosted checkout page)
    const response = await fetch('https://api.polar.sh/api/v1/checkouts/custom', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.polar.accessToken}`,
      },
      body: JSON.stringify({
        product_price_id: priceId,
        customer_id: polarCustomerId,
        organization_id: config.polar.organizationId,
        success_url: 'http://localhost:3000/dashboard?checkout=success',
      }),
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(`Failed to create checkout session: ${JSON.stringify(errorData)}`);
    }

    const data = await response.json();
    return { checkoutUrl: data.url };
  }
}

export const billingService = new BillingService();
