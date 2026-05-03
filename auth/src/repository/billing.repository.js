// ──────────────────────────────────────────────────────────────
// billing.repository.js — Database operations for Billing
// Handles the subscriptions table and polar_customer_id
// ──────────────────────────────────────────────────────────────

import { getPool } from '../db.js';

export class BillingRepository {
  /**
   * Links a Polar Customer ID to a user.
   */
  async setPolarCustomerId(userId, polarCustomerId) {
    const pool = getPool();
    const result = await pool.query(
      `UPDATE users 
       SET polar_customer_id = $1, updated_at = NOW() 
       WHERE id = $2 
       RETURNING id, polar_customer_id`,
      [polarCustomerId, userId]
    );
    return result.rows[0];
  }

  /**
   * Gets the Polar Customer ID for a user.
   */
  async getPolarCustomerId(userId) {
    const pool = getPool();
    const result = await pool.query(
      `SELECT polar_customer_id FROM users WHERE id = $1`,
      [userId]
    );
    return result.rows[0]?.polar_customer_id || null;
  }

  /**
   * Creates a new subscription record.
   */
  async createSubscription(subData) {
    const pool = getPool();
    const {
      userId,
      polarSubId,
      planTier,
      currentPeriodEnd,
      status,
      prorationMetadata = {}
    } = subData;

    const result = await pool.query(
      `INSERT INTO subscriptions 
        (user_id, polar_sub_id, plan_tier, current_period_end, status, proration_metadata)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING *`,
      [userId, polarSubId, planTier, currentPeriodEnd, status, JSON.stringify(prorationMetadata)]
    );
    return result.rows[0];
  }

  /**
   * Retrieves active subscriptions for a user to prevent duplicates.
   */
  async getActiveSubscriptions(userId) {
    const pool = getPool();
    const result = await pool.query(
      `SELECT * FROM subscriptions 
       WHERE user_id = $1 AND status = 'active'`,
      [userId]
    );
    return result.rows;
  }

  /**
   * Updates an existing subscription.
   */
  async updateSubscriptionStatus(polarSubId, status, currentPeriodEnd = null) {
    const pool = getPool();
    
    let query = `UPDATE subscriptions SET status = $1, updated_at = NOW()`;
    const params = [status, polarSubId];
    
    if (currentPeriodEnd) {
      query += `, current_period_end = $3`;
      params.push(currentPeriodEnd);
    }
    
    query += ` WHERE polar_sub_id = $2 RETURNING *`;
    
    const result = await pool.query(query, params);
    return result.rows[0];
  }
}

export const billingRepository = new BillingRepository();
