// ── Strat Ai — Playwright E2E configuration ─────────────────────────────────
//
// REPAIRED, not rewritten from scratch. The previous version targeted
// `baseURL: http://localhost:1420` and ran `npx next dev --port 1420`. 1420 was the **Tauri** dev
// port; the desktop shell no longer exists, so nothing ever listened there and the whole suite
// could only have failed — which nobody noticed, because no CI job ran it.
//
// Two deliberate changes beyond the port:
//
//   * `next start` against a `build:web` output, NOT `next dev`. A route can typecheck, unit-test
//     green, and still fail to REGISTER in a production build (see CLAUDE.md §0) — and the
//     multi-session workspace is a new dynamic route, which is exactly that failure mode. Testing
//     the dev server would not catch it.
//   * `reuseExistingServer: false` in CI. Reusing whatever happens to be on :3000 is convenient
//     locally and dishonest in CI, where it could silently test a stale build.
//
// The web server is started by the CI job rather than here, because the run also needs a stubbed
// deep-quant and an identity stub, and Playwright's `webServer` cannot express the ordering.

import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.E2E_PORT ?? 3000);
const BASE_URL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: './tests',
  // Session isolation is asserted by driving two sessions inside ONE test, deliberately. Running
  // spec files in parallel against a single shared deep-quant would let one test's runs appear in
  // another's session list, and the failure would look like the isolation bug rather than the test
  // harness.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // No retries. A retry on a streaming/isolation test converts a real intermittent routing bug
  // into a green run, and those are precisely the bugs this suite exists to catch.
  retries: 0,
  reporter: process.env.CI
    ? [['github'], ['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]]
    : [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  // Generous, because a real streamed run is deliberately incremental in the stub.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'fq-desktop',
      // `testIgnore` is required, not tidiness: a project with only `use` picks up EVERY spec in
      // `testDir`, so without this the 360 px assertions ran at desktop width. They failed, which was
      // lucky — a viewport assertion that happens to hold at the wrong width would have passed and
      // proved nothing.
      testIgnore: /.*\.mobile\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // 360 px is the width the migration plan calls out for the workspace, and it is the one
      // assertion jsdom cannot make: it has no layout engine, so a unit test claiming something is
      // "visible at 360 px" would be fiction. Only the mobile-specific spec runs here.
      name: 'fq-mobile',
      testMatch: /.*\.mobile\.spec\.ts/,
      use: { ...devices['Pixel 5'], viewport: { width: 360, height: 780 } },
    },
  ],
  webServer: process.env.E2E_NO_WEBSERVER
    ? undefined
    : {
        // Production output, matching CI. `build:web` must have been run first.
        command: `npx next start --port ${PORT}`,
        url: `${BASE_URL}/`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
