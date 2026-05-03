// ──────────────────────────────────────────────────────────────
// billing.sync.js — Self-Healing Sync Engine
// Periodically reconciles local subscription statuses with Polar API
// to catch any missed webhooks and maintain accurate access locks.
// ──────────────────────────────────────────────────────────────

import { getPool } from '../db.js';
import { config } from '../config.js';
import { billingRepository } from '../repository/billing.repository.js';
import { BILLING_CATALOG } from '../config/billing.catalog.js';

export class BillingSyncEngine {
  constructor() {
    this.isRunning = false;
    this.intervalId = null;
  }

  /**
   * Starts the sync engine to run at a given interval (default 24h).
   */
  start(intervalMs = 24 * 60 * 60 * 1000) {
    if (this.intervalId) {
      clearInterval(this.intervalId);
    }
    console.log('[BILLING-SYNC] Starting Self-Healing Sync Engine...');
    this.intervalId = setInterval(() => this.runSyncTask(), intervalMs);
    
    // Optionally run immediately on start (uncomment if desired)
    // this.runSyncTask();
  }

  /**
   * Stops the sync engine.
   */
  stop() {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
      console.log('[BILLING-SYNC] Stopped Self-Healing Sync Engine.');
    }
  }

  /**
   * Executes the reconciliation task.
   */
  async runSyncTask() {
    if (this.isRunning) return;
    this.isRunning = true;
    console.log('[BILLING-SYNC] Executing reconciliation task...');

    const pool = getPool();
    try {
      // 1. Fetch all local subscriptions that we believe are active
      // (Could be expanded to check past_due or recently canceled ones too)
      const result = await pool.query(`SELECT polar_sub_id, status, plan_tier FROM subscriptions WHERE status = 'active'`);
      const activeSubs = result.rows;

      let reconciledCount = 0;

      // 2. Query Polar API for each (Rate limiting should be considered in prod, maybe batching)
      for (const sub of activeSubs) {
        try {
          const response = await fetch(`https://api.polar.sh/api/v1/subscriptions/${sub.polar_sub_id}`, {
            headers: {
              'Authorization': `Bearer ${config.polar.accessToken}`
            }
          });

          if (!response.ok) {
            console.error(`[BILLING-SYNC] Failed to fetch subscription ${sub.polar_sub_id} from Polar`);
            continue;
          }

          const polarData = await response.json();
          const trueStatus = polarData.status;
          const truePriceId = polarData.price_id || polarData.product_price_id;
          const trueTier = BILLING_CATALOG[truePriceId]?.tier;

          // 3. Reconcile differences
          let needsUpdate = false;

          if (sub.status !== trueStatus) {
             console.log(`[BILLING-SYNC] Discrepancy found for ${sub.polar_sub_id}: Local(${sub.status}) vs Polar(${trueStatus})`);
             needsUpdate = true;
          }

          if (trueTier && sub.plan_tier !== trueTier) {
             console.log(`[BILLING-SYNC] Tier discrepancy found for ${sub.polar_sub_id}: Local(${sub.plan_tier}) vs Polar(${trueTier})`);
             await pool.query('UPDATE subscriptions SET plan_tier = $1 WHERE polar_sub_id = $2', [trueTier, sub.polar_sub_id]);
          }

          if (needsUpdate) {
             await billingRepository.updateSubscriptionStatus(sub.polar_sub_id, trueStatus, new Date(polarData.current_period_end).toISOString());
             reconciledCount++;
          }

        } catch (err) {
          console.error(`[BILLING-SYNC] Error reconciling ${sub.polar_sub_id}:`, err.message);
        }
      }

      console.log(`[BILLING-SYNC] Task complete. Reconciled ${reconciledCount} subscriptions.`);
    } catch (err) {
      console.error('[BILLING-SYNC] Critical error in sync task:', err);
    } finally {
      this.isRunning = false;
    }
  }
}

export const billingSyncEngine = new BillingSyncEngine();
