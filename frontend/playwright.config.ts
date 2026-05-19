// ── Alpha Suite V3 — Playwright E2E Configuration ───────────────────────────
//
// Configures Playwright to test the frontend UI via the Next.js dev server.
// In ALPHA_TEST_MODE, the Tauri IPC calls are mocked at the frontend layer,
// allowing us to test the full React UI without the native Tauri shell.
//
// Run: npx cross-env ALPHA_TEST_MODE=1 npx playwright test

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['list'],
  ],
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    // Connect to the Next.js dev server (Tauri's frontend port)
    baseURL: 'http://localhost:1420',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'alpha-suite-e2e',
      use: {
        ...devices['Desktop Chrome'],
      },
    },
  ],
  // Start the Next.js dev server on port 1420 (same port Tauri uses)
  webServer: {
    command: 'npx next dev --port 1420',
    url: 'http://localhost:1420',
    reuseExistingServer: true,
    timeout: 60_000,
    env: {
      ALPHA_TEST_MODE: '1',
    },
  },
});
