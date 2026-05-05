import { PrismaClient } from '@prisma/client';
import { PrismaPg } from '@prisma/adapter-pg';
import pkg from 'pg';
const { Pool } = pkg;
import { config } from './config.js';

let _prisma = null;
let _pool = null;

export function getPool() {
  if (!_prisma) {
    _pool = new Pool({ connectionString: config.postgresUrl });
    const adapter = new PrismaPg(_pool);
    _prisma = new PrismaClient({ adapter });
    console.log('[AUTH-DB] Prisma client initialized with pg adapter.');
  }
  return _prisma;
}

export async function closePool() {
  if (_prisma) {
    await _prisma.$disconnect();
    _prisma = null;
    console.log('[AUTH-DB] Prisma client disconnected.');
  }
}
