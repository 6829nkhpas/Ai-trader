import { prisma } from '../db';

export class BrokerRepository {
  async upsertBrokerConnection(userId: string, data: Partial<any>) {
    return prisma.brokerConnection.upsert({
      where: { userId },
      update: data,
      create: {
        userId,
        accessToken: data.accessToken || '',
        ...data
      }
    });
  }
}
