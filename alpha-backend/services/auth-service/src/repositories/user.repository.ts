import { prisma } from '../db';

export class UserRepository {
  async findByEmail(email: string) {
    return prisma.user.findUnique({
      where: { email },
      include: { brokerConnection: true }
    });
  }

  async findById(id: string) {
    return prisma.user.findUnique({
      where: { id },
      include: { brokerConnection: true }
    });
  }

  async findFirstUser() {
    return prisma.user.findFirst();
  }

  async createUser(data: { id?: string; email: string; password: string; name?: string; tier?: string }) {
    return prisma.user.create({
      data: {
        id: data.id,
        email: data.email,
        password: data.password,
        name: data.name,
        tier: data.tier || 'FREE'
      },
      include: { brokerConnection: true }
    });
  }

  async updateTier(id: string, tier: string) {
    return prisma.user.update({
      where: { id },
      data: { tier }
    });
  }
}
