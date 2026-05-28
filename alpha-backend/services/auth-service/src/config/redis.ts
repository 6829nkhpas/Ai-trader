import Redis from 'ioredis';
import dotenv from 'dotenv';

dotenv.config();

const redisUrl = process.env.REDIS_URL || 'redis://127.0.0.1:6379';

console.log(`[Redis Config] Connecting to Redis at: ${redisUrl}`);

export const redis = new Redis(redisUrl, {
  maxRetriesPerRequest: 3,
  retryStrategy(times) {
    const delay = Math.min(times * 100, 2000);
    return delay;
  }
});

redis.on('connect', () => {
  console.log('[Redis Connection] Successfully connected to Redis Server.');
});

redis.on('error', (err) => {
  console.error('[Redis Connection] Error connecting to Redis Server:', err.message);
});
