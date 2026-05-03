// ──────────────────────────────────────────────────────────────
// db.js — PostgreSQL connection pool for the auth service
// Uses node-postgres (pg) with a lazy singleton pool.
// ──────────────────────────────────────────────────────────────

import pg from 'pg';
import { config } from './config.js';

const { Pool } = pg;

let _pool = null;

/**
 * Returns the singleton PostgreSQL connection pool.
 * Creates it on first call; reuses on subsequent calls.
 */
export function getPool() {
  if (!_pool) {
    _pool = new Pool({
      connectionString: config.postgresUrl,
      max: 10,                    // Max connections in pool
      idleTimeoutMillis: 30_000,  // Close idle connections after 30s
      connectionTimeoutMillis: 5_000,
    });

    _pool.on('error', (err) => {
      console.error('[AUTH-DB] Unexpected pool error:', err.message);
    });

    console.log('[AUTH-DB] PostgreSQL connection pool initialized.');
  }

  return _pool;
}

/**
 * Gracefully shut down the pool (for SIGINT / SIGTERM handlers).
 */
export async function closePool() {
  if (_pool) {
    await _pool.end();
    _pool = null;
    console.log('[AUTH-DB] PostgreSQL pool closed.');
  }
}
