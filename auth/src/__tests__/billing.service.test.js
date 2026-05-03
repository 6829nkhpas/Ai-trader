// ──────────────────────────────────────────────────────────────
// billing.service.test.js
// Tests Polar integration logic and duplicate subscription guards
// ──────────────────────────────────────────────────────────────

import { describe, it, mock, beforeEach } from 'node:test';
import assert from 'node:assert';
import { billingService } from '../services/billing.service.js';
import { billingRepository } from '../repository/billing.repository.js';
import { getPool } from '../db.js';
import * as userRepository from '../repository/user.repository.js';

describe('Billing Service - Polar Integration', () => {
  beforeEach(() => {
    // Reset mocks
    mock.restoreAll();
  });

  it('should provision a new Polar customer if one does not exist', async () => {
    // Mock DB queries
    mock.method(billingRepository, 'getPolarCustomerId', async () => null);
    mock.method(billingRepository, 'setPolarCustomerId', async () => {});
    
    // Mock user repository
    mock.method(userRepository, 'findUserById', async () => ({ email: 'test@example.com' }));
    
    // Mock DB Pool
    const mockClient = { release: () => {} };
    mock.method(getPool(), 'connect', async () => mockClient);

    // Mock fetch for Polar API
    global.fetch = mock.fn(async () => {
      return {
        ok: true,
        json: async () => ({ id: 'polar_cust_123' })
      };
    });

    const customerId = await billingService.getOrProvisionCustomer('user_123');
    
    assert.strictEqual(customerId, 'polar_cust_123');
    assert.strictEqual(global.fetch.mock.calls.length, 1);
    
    const fetchArgs = global.fetch.mock.calls[0].arguments;
    assert.strictEqual(fetchArgs[0], 'https://api.polar.sh/api/v1/customers');
    assert.strictEqual(fetchArgs[1].method, 'POST');
    
    const body = JSON.parse(fetchArgs[1].body);
    assert.strictEqual(body.email, 'test@example.com');
  });

  it('should return existing Polar customer ID without calling API', async () => {
    mock.method(billingRepository, 'getPolarCustomerId', async () => 'existing_polar_123');
    global.fetch = mock.fn(); // Should not be called

    const customerId = await billingService.getOrProvisionCustomer('user_123');
    
    assert.strictEqual(customerId, 'existing_polar_123');
    assert.strictEqual(global.fetch.mock.calls.length, 0);
  });

  it('should prevent creating checkout if active subscription exists for same product', async () => {
    mock.method(billingService, 'getOrProvisionCustomer', async () => 'polar_cust_123');
    
    // Mock active subscriptions to simulate an existing Weekly plan
    mock.method(billingRepository, 'getActiveSubscriptions', async () => [
      { plan_tier: 'Weekly', status: 'active' }
    ]);

    try {
      await billingService.createCheckoutSession('user_123', 'price_weekly');
      assert.fail('Should have thrown conflict error');
    } catch (err) {
      assert.match(err.message, /Conflict: User already has an active subscription/);
    }
  });

  it('should create checkout session if no active subscription exists', async () => {
    mock.method(billingService, 'getOrProvisionCustomer', async () => 'polar_cust_123');
    mock.method(billingRepository, 'getActiveSubscriptions', async () => []);
    
    global.fetch = mock.fn(async () => ({
      ok: true,
      json: async () => ({ url: 'https://checkout.polar.sh/some-session' })
    }));

    const result = await billingService.createCheckoutSession('user_123', 'price_monthly');
    
    assert.strictEqual(result.checkoutUrl, 'https://checkout.polar.sh/some-session');
    
    const fetchArgs = global.fetch.mock.calls[0].arguments;
    assert.strictEqual(fetchArgs[0], 'https://api.polar.sh/api/v1/checkouts/custom');
    
    const body = JSON.parse(fetchArgs[1].body);
    assert.strictEqual(body.product_price_id, 'price_monthly');
    assert.strictEqual(body.customer_id, 'polar_cust_123');
  });
});
