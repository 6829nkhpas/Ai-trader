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
  },
});
