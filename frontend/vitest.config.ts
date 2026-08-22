import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

// Vitest configuration for the charting suite's pure engines and their
// property-based tests (fast-check). Tests live alongside the charting module
// under `src/charting/__tests__` and use the `@/*` path alias that mirrors the
// Next.js tsconfig.
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: 'node',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // `lib/env.ts` throws at import time when these are unset, which took out 13
    // test FILES at collection (every suite that transitively imports
    // `useAuthStore`). Vitest does not read `.env.local` — that is a Next.js
    // loader feature — so the suite would otherwise depend on a gitignored file
    // being present, and pass or fail differently per machine. These are inert
    // placeholders: no test performs a real request to either host.
    env: {
      NEXT_PUBLIC_API_BASE_URL: 'http://127.0.0.1:0/api-test',
      NEXT_PUBLIC_DASHBOARD_URL: 'http://127.0.0.1:0/dashboard-test',
    },
  },
});
