import { getPool } from '../db.js';

export class BillingRepository {
  /**
   * Links a Polar Customer ID to a user.
   */
  async setPolarCustomerId(userId, polarCustomerId) {
    const prisma = getPool();
    const user = await prisma.users.update({
      where: { id: userId },
      data: {
        polar_customer_id: polarCustomerId,
        updated_at: new Date()
      },
      select: { id: true, polar_customer_id: true }
    });
    return user;
  }

  /**
   * Gets the Polar Customer ID for a user.
   */
  async getPolarCustomerId(userId) {
    const prisma = getPool();
    const user = await prisma.users.findUnique({
      where: { id: userId },
      select: { polar_customer_id: true }
    });
    return user?.polar_customer_id || null;
  }

  /**
   * Creates a new subscription record.
   */
  async createSubscription(subData) {
    const prisma = getPool();
    const {
      userId,
      polarSubId,
      planTier,
      currentPeriodEnd,
      status,
      prorationMetadata = {}
    } = subData;

    const sub = await prisma.subscriptions.create({
      data: {
        user_id: userId,
        polar_sub_id: polarSubId,
        plan_tier: planTier,
        current_period_end: new Date(currentPeriodEnd),
        status: status,
        proration_metadata: prorationMetadata
      }
    });
    return sub;
  }

  /**
   * Retrieves active subscriptions for a user to prevent duplicates.
   */
  async getActiveSubscriptions(userId) {
    const prisma = getPool();
    const subs = await prisma.subscriptions.findMany({
      where: {
        user_id: userId,
        status: 'active'
      }
    });
    return subs;
  }

  /**
   * Retrieves a subscription by Polar ID.
   */
  async getSubscriptionByPolarId(polarSubId) {
    const prisma = getPool();
    const sub = await prisma.subscriptions.findUnique({
      where: { polar_sub_id: polarSubId }
    });
    return sub || null;
  }

  /**
   * Updates an existing subscription status, end period, and metadata.
   */
  async updateSubscriptionStatus(polarSubId, status, currentPeriodEnd = null, prorationMetadata = null) {
    const prisma = getPool();
    
    const data = {
      status,
      updated_at: new Date()
    };
    
    if (currentPeriodEnd) {
      data.current_period_end = new Date(currentPeriodEnd);
    }
    
    if (prorationMetadata) {
      data.proration_metadata = prorationMetadata;
    }
    
    const sub = await prisma.subscriptions.update({
      where: { polar_sub_id: polarSubId },
      data
    });
    
    return sub;
  }
}

export const billingRepository = new BillingRepository();
