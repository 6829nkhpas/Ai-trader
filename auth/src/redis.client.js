// ──────────────────────────────────────────────────────────────
// redis.client.js — IoRedis singleton for JTI blacklist
// Lazy-initialized, cluster-ready Redis client.
// ──────────────────────────────────────────────────────────────

import Redis from 'ioredis';

let _client = null;

/**
 * Returns the singleton Redis client.
 * Creates on first call; reuses on subsequent calls.
 * @returns {Redis}
 */
export function getRedisClient() {
  if (!_client) {
    const url = process.env.REDIS_URL;
    _client = new Redis(url, {
      maxRetriesPerRequest: 3,
      retryStrategy(times) {
        const delay = Math.min(times * 200, 3000);
        return delay;
      },
      lazyConnect: false,
    });

    _client.on('connect', () => {
      console.log('[AUTH-REDIS] Connected to Redis.');
    });

    _client.on('error', (err) => {
      console.error('[AUTH-REDIS] Redis error:', err.message);
    });
  }

  return _client;
}

/**
 * Gracefully shut down the Redis connection.
 */
export async function closeRedis() {
  if (_client) {
    await _client.quit();
    _client = null;
    console.log('[AUTH-REDIS] Redis connection closed.');
  }
}
