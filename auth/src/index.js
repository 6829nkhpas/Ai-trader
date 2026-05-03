// ──────────────────────────────────────────────────────────────
// index.js — Auth service entry point (Fastify HTTP server)
// Bootstraps the app: loads config, registers middleware,
// wires routes, and starts listening.
// ──────────────────────────────────────────────────────────────

import { config } from './config.js';
import { closePool } from './db.js';
import { closeRedis } from './redis.client.js';
import { registerAuthRoutes } from './routes/auth.routes.js';
import { registerKycRoutes } from './routes/kyc.routes.js';
import { registerWebhookRoutes } from './routes/webhook.routes.js';
import { registerTradeRoutes } from './routes/trade.routes.js';
import { registerBillingRoutes } from './routes/billing.routes.js';
import { billingSyncEngine } from './services/billing.sync.js';
import { registerErrorHandler } from './middleware/error.handler.js';
import Fastify from 'fastify';
import cookie from '@fastify/cookie';
import helmet from '@fastify/helmet';

const app = Fastify({ logger: true });

// ── Plugins ──────────────────────────────────────────────────
app.register(cookie, {
  secret: config.authPepper, // use pepper to sign cookies if needed
  hook: 'onRequest',
});

// ── Middleware ───────────────────────────────────────────────
app.register(helmet); // Security headers (HSTS, CSP, XSS, etc.)
registerErrorHandler(app);

// ── Routes ──────────────────────────────────────────────────
registerAuthRoutes(app);
registerKycRoutes(app);
registerWebhookRoutes(app);
registerTradeRoutes(app);
registerBillingRoutes(app);

// ── Startup ─────────────────────────────────────────────────
const start = async () => {
  try {
    await app.listen({ port: config.authPort, host: '0.0.0.0' });
    console.log(`[AUTH] Identity vault listening on :${config.authPort}`);
    
    // Start self-healing sync engine
    billingSyncEngine.start();
  } catch (err) {
    console.error('[AUTH] Failed to start:', err);
    process.exit(1);
  }
};

// ── Graceful shutdown ───────────────────────────────────────
const shutdown = async () => {
  console.log('[AUTH] Shutting down...');
  billingSyncEngine.stop();
  await app.close();
  await closePool();
  await closeRedis();
  process.exit(0);
};
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

start();
