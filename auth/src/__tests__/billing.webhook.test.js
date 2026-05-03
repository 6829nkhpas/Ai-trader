// ──────────────────────────────────────────────────────────────
// billing.webhook.test.js
// Tests Webhook processing and Billing Guard logic.
// ──────────────────────────────────────────────────────────────

import { describe, it, mock, beforeEach } from 'node:test';
import assert from 'node:assert';
import { handlePolarWebhook } from '../controllers/billing.webhook.controller.js';
import { requireActiveSubscription } from '../middleware/billing.guard.js';
import { billingRepository } from '../repository/billing.repository.js';
import { config } from '../config.js';

describe('Billing Ignition (Webhooks & Guard)', () => {
  beforeEach(() => {
    mock.restoreAll();
    // Bypass signature check for tests by unsetting the secret temporarily
    config.polar.webhookSecret = null;
  });

  describe('Webhook Listener', () => {
    it('should revoke subscription on subscription.revoked event', async () => {
      mock.method(billingRepository, 'updateSubscriptionStatus', async () => {});

      const mockRequest = {
        body: {
          type: 'subscription.revoked',
          data: {
            id: 'polar_sub_123',
            status: 'canceled',
            metadata: { user_id: 'user_123' }
          }
        },
        headers: {},
        log: { info: mock.fn(), warn: mock.fn(), error: mock.fn() }
      };

      const mockReply = {
        status: mock.fn(() => mockReply),
        send: mock.fn()
      };

      await handlePolarWebhook(mockRequest, mockReply);

      assert.strictEqual(mockReply.status.mock.calls[0].arguments[0], 200);
      assert.strictEqual(billingRepository.updateSubscriptionStatus.mock.calls.length, 1);
      assert.strictEqual(billingRepository.updateSubscriptionStatus.mock.calls[0].arguments[0], 'polar_sub_123');
      assert.strictEqual(billingRepository.updateSubscriptionStatus.mock.calls[0].arguments[1], 'revoked');
    });

    it('should update subscription on subscription.updated event', async () => {
      mock.method(billingRepository, 'getSubscriptionByPolarId', async () => ({
        polar_sub_id: 'polar_sub_123',
        plan_tier: 'Weekly'
      }));
      mock.method(billingRepository, 'updateSubscriptionStatus', async () => {});

      const mockRequest = {
        body: {
          type: 'subscription.updated',
          data: {
            id: 'polar_sub_123',
            status: 'past_due',
            current_period_end: '2026-06-01T00:00:00Z',
            price_id: 'price_weekly',
            metadata: { user_id: 'user_123' }
          }
        },
        headers: {},
        log: { info: mock.fn(), warn: mock.fn(), error: mock.fn() }
      };

      const mockReply = {
        status: mock.fn(() => mockReply),
        send: mock.fn()
      };

      await handlePolarWebhook(mockRequest, mockReply);

      assert.strictEqual(mockReply.status.mock.calls[0].arguments[0], 200);
      assert.strictEqual(billingRepository.updateSubscriptionStatus.mock.calls.length, 1);
      assert.strictEqual(billingRepository.updateSubscriptionStatus.mock.calls[0].arguments[1], 'past_due');
    });
  });

  describe('The Billing Guard', () => {
    it('should deny access (402) if no active subscription exists', async () => {
      mock.method(billingRepository, 'getActiveSubscriptions', async () => []);

      const mockRequest = {
        user: { sub: 'user_123' },
        log: { error: mock.fn() }
      };

      const mockReply = {
        status: mock.fn(() => mockReply),
        send: mock.fn()
      };

      await requireActiveSubscription(mockRequest, mockReply);

      assert.strictEqual(mockReply.status.mock.calls[0].arguments[0], 402);
      assert.match(mockReply.send.mock.calls[0].arguments[0].error, /Payment Required/);
    });

    it('should deny access (402) if active subscription is past current_period_end', async () => {
      const pastDate = new Date();
      pastDate.setDate(pastDate.getDate() - 1); // Yesterday

      mock.method(billingRepository, 'getActiveSubscriptions', async () => [
        { status: 'active', current_period_end: pastDate.toISOString() }
      ]);

      const mockRequest = {
        user: { sub: 'user_123' },
        log: { error: mock.fn() }
      };

      const mockReply = {
        status: mock.fn(() => mockReply),
        send: mock.fn()
      };

      await requireActiveSubscription(mockRequest, mockReply);

      assert.strictEqual(mockReply.status.mock.calls[0].arguments[0], 402);
      assert.match(mockReply.send.mock.calls[0].arguments[0].details, /expired/);
    });

    it('should allow access if active valid subscription exists', async () => {
      const futureDate = new Date();
      futureDate.setDate(futureDate.getDate() + 10); // 10 days in future

      mock.method(billingRepository, 'getActiveSubscriptions', async () => [
        { status: 'active', current_period_end: futureDate.toISOString() }
      ]);

      const mockRequest = {
        user: { sub: 'user_123' },
        log: { error: mock.fn() }
      };

      const mockReply = {
        status: mock.fn(() => mockReply),
        send: mock.fn()
      };

      await requireActiveSubscription(mockRequest, mockReply);

      // Reply is not called for successful middleware unless an error happens
      assert.strictEqual(mockReply.status.mock.calls.length, 0);
      assert.strictEqual(mockRequest.subscription.status, 'active');
    });
  });
});
