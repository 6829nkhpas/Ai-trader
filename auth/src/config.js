// ──────────────────────────────────────────────────────────────
// config.js — Environment validation & constants
// Loads .env from the monorepo root, validates all required
// auth-specific environment variables, and exports them.
// ──────────────────────────────────────────────────────────────

import dotenv from 'dotenv';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Load .env from monorepo root (two levels up from auth/src/)
const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

// ── Required Variables ──────────────────────────────────────
const required = [
  'POSTGRES_AUTH_URL',
  'AUTH_PEPPER',
];

for (const key of required) {
  if (!process.env[key]) {
    console.error(`FATAL: Missing required environment variable: ${key}`);
    process.exit(1);
  }
}

// ── Pepper Validation ───────────────────────────────────────
if (process.env.AUTH_PEPPER.length < 32) {
  console.error('FATAL: AUTH_PEPPER must be at least 32 characters.');
  process.exit(1);
}

// ── Exported Config ─────────────────────────────────────────
export const config = Object.freeze({
  // PostgreSQL
  postgresUrl: process.env.POSTGRES_AUTH_URL,

  // Cryptographic pepper — prepended to passwords before Argon2id hashing
  authPepper: process.env.AUTH_PEPPER,

  // HTTP server
  authPort: parseInt(process.env.AUTH_PORT || '3001', 10),

  // OAuth
  googleClientId: process.env.GOOGLE_CLIENT_ID,

  // Password complexity defaults
  password: {
    minLength:       12,
    maxLength:       128,    // Prevent DoS via Argon2id memory cost
    requireUppercase: true,
    requireLowercase: true,
    requireDigit:     true,
    requireSpecial:   true,
  },

  // JWT Session Layer
  jwt: {
    privateKeyPath: process.env.JWT_PRIVATE_KEY_PATH || './keys/private.pem',
    publicKeyPath:  process.env.JWT_PUBLIC_KEY_PATH  || './keys/public.pem',
    accessTtl:      parseInt(process.env.JWT_ACCESS_TTL_SECONDS  || '900', 10),
    refreshTtl:     parseInt(process.env.JWT_REFRESH_TTL_SECONDS || '604800', 10),
    issuer:         'ai-trade-auth',
  },

  // Polar API Config
  polar: {
    accessToken: process.env.POLAR_ACCESS_TOKEN,
    organizationId: process.env.POLAR_ORGANIZATION_ID,
    webhookSecret: process.env.POLAR_WEBHOOK_SECRET,
  },
});
