import { prisma } from '../db';

export class SubscriptionRepository {
  async upsertSubscription(userId: string, data: { status: string; currentPeriodEnd: Date; stripeCustomerId?: string }) {
    return prisma.subscription.upsert({
      where: { userId },
      update: {
        stripeCustomerId: data.stripeCustomerId || 'phonepe_merchant_cust',
        status: data.status,
        currentPeriodEnd: data.currentPeriodEnd
      },
      create: {
        userId,
        stripeCustomerId: data.stripeCustomerId || 'phonepe_merchant_cust',
        status: data.status,
        currentPeriodEnd: data.currentPeriodEnd
      }
    });
  }
}
