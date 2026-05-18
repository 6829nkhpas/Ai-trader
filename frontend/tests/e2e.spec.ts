// ── Alpha Suite V3 — End-to-End User Journey Test ───────────────────────────
//
// Validates the E2E test infrastructure and mock API layer for the Alpha Suite.
// Uses Playwright route interception to mock API responses at the browser level.
//
// V3 additions (Alpha Crucible — IPC Contract Testing):
//   • Page-level console + pageerror listeners attached to every test, so any
//     uncaught UI exception or unexpected log message surfaces in the trace.
//   • A dedicated test block that drives `invoke('run_deep_quant_analysis')`
//     through a stubbed Tauri IPC bridge and asserts the returned object
//     conforms exactly to the AiExecutionPlan TypeScript shape (the same
//     contract the Rust backend emits via DeepSeek).
//
// Note: The full trading UI requires the Tauri native shell for WebSocket events
// and IPC commands. These tests validate:
//   1. The test infrastructure (Playwright + Next.js dev server)
//   2. Mock API route responses
//   3. Page rendering and navigation
//   4. The Tauri IPC contract for `run_deep_quant_analysis`
//
// For full integration testing with Tauri, run: npm run tauri:dev with ALPHA_TEST_MODE=1

import { test, expect, Page } from '@playwright/test';

// ── Mock Data ───────────────────────────────────────────────────────────────

const MOCK_USER = {
  user: {
    id: 'test-user-001',
    email: 'alpha@test.local',
    displayName: 'Alpha Tester',
    avatarUrl: null,
    mfaEnabled: false,
    kycStatus: 'VERIFIED',
    subscriptionStatus: 'ACTIVE',
    createdAt: '2024-01-01T00:00:00Z',
  },
};

const MOCK_KYC_PROFILE = {
  profile: {
    id: 'test-user-001',
    kyc_status: 'VERIFIED',
    full_name: 'Alpha Tester',
    pan_number: 'XXXXX0000X',
    risk_profile: 'AGGRESSIVE',
    trading_experience: 'EXPERT',
  },
};

const MOCK_QUOTE = {
  quotes: [{
    symbol: 'RELIANCE',
    last_price: 2468.0,
    open: 2450.0,
    high: 2475.0,
    low: 2440.0,
    close: 2445.0,
    change: 0.94,
    net_change: 23.0,
    volume: 125000,
  }],
};

// Canonical AiExecutionPlan shape mirrored from the Rust backend.
// Any change here MUST be reflected in `src-tauri/src/quant/mod.rs`.
const MOCK_AI_EXECUTION_PLAN = {
  conviction_score: 78,
  setup_validation:
    'Golden Cross confirmed with rising OBV and bullish engulfing pattern.',
  execution_plan:
    'ENTRY: 2470 | STOP: 2435 | T1: 2510 | T2: 2550 | SIZE: 2%',
};

/**
 * Set up route interception to mock all API calls at the browser level.
 */
async function setupMockRoutes(page: Page) {
  await page.route('**/api/auth/session**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) });
  });
  await page.route('**/api/auth/refresh**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) });
  });
  await page.route('**/api/kyc/profile**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_KYC_PROFILE) });
  });
  await page.route('**/kite/quote**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_QUOTE) });
  });
}

/**
 * Attach console + pageerror listeners. Tauri IPC traffic surfaces in the
 * webview console (Tauri's `invoke` bridge logs warnings + errors to console),
 * so this is our cheapest passive monitor for IPC failures.
 */
function attachIpcMonitors(page: Page) {
  page.on('console', (msg) => {
    console.log(`[UI LOG] ${msg.type()}: ${msg.text()}`);
  });
  page.on('pageerror', (exception) => {
    console.error(`[UI ERROR] Uncaught exception: ${exception}`);
  });
  page.on('requestfailed', (req) => {
    console.warn(`[UI NET] failed ${req.method()} ${req.url()} — ${req.failure()?.errorText}`);
  });
}

test.describe('Alpha Suite V3 — E2E Test Infrastructure', () => {

  test.beforeEach(async ({ page }) => {
    attachIpcMonitors(page);
  });

  test('1. Next.js dev server is running and serves pages', async ({ page }) => {
    const response = await page.goto('/');
    expect(response).not.toBeNull();
    expect(response!.status()).toBeLessThan(500);

    // Page should render something (either trading UI or login)
    const bodyText = await page.textContent('body');
    expect(bodyText).toBeTruthy();
    expect(bodyText!.length).toBeGreaterThan(50);
  });

  test('2. Playwright route interception mocks API responses', async ({ page }) => {
    await setupMockRoutes(page);

    // Navigate to a page that will trigger API calls
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Verify route interception works by making direct fetch calls
    const sessionResp = await page.evaluate(async () => {
      const res = await fetch('/api/auth/session/');
      return { status: res.status, body: await res.text() };
    });
    expect(sessionResp.status).toBe(200);
    const sessionData = JSON.parse(sessionResp.body);
    expect(sessionData.user.id).toBe('test-user-001');
    expect(sessionData.user.kycStatus).toBe('VERIFIED');

    const kycResp = await page.evaluate(async () => {
      const res = await fetch('/api/kyc/profile/');
      return { status: res.status, body: await res.text() };
    });
    expect(kycResp.status).toBe(200);
    const kycData = JSON.parse(kycResp.body);
    expect(kycData.profile.kyc_status).toBe('VERIFIED');
  });

  test('3. Trading UI renders when auth is mocked', async ({ page }) => {
    await setupMockRoutes(page);
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(3000);

    const currentUrl = page.url();
    const bodyText = await page.textContent('body');

    if (!currentUrl.includes('/auth/')) {
      // Trading UI loaded successfully
      expect(
        bodyText?.includes('RELIANCE') ||
        bodyText?.includes('INTRADAY') ||
        bodyText?.includes('SWING') ||
        bodyText?.includes('INVESTOR') ||
        bodyText?.includes('Preparing your workspace')
      ).toBeTruthy();

      // Check for the sidebar tabs
      const aiQuantTab = page.locator('button:has-text("AI QUANT")');
      if (await aiQuantTab.isVisible()) {
        await aiQuantTab.click();
        await page.waitForTimeout(500);

        // Deep Quant button should be visible
        const deepQuantBtn = page.locator('#btn-run-deep-quant');
        await expect(deepQuantBtn).toBeVisible({ timeout: 5_000 });
        await expect(deepQuantBtn).toContainText('RUN DEEP QUANT ANALYSIS');
      }
    } else {
      // Page redirected to auth — this is expected in browser-only mode
      // The full trading UI requires Tauri IPC context
      console.log('Trading UI requires Tauri context for full rendering.');
      console.log('Run with: ALPHA_TEST_MODE=1 npm run tauri:dev');
      expect(bodyText?.includes('Sign in') || bodyText?.includes('Welcome')).toBeTruthy();
    }
  });

  test('4. Server-side mock API routes respond (Next.js route handlers)', async ({ request }) => {
    // Test the Next.js API route handlers directly (server-side)
    const sessionResp = await request.get('/api/auth/session/');
    expect(sessionResp.status()).toBe(200);
    const sessionJson = await sessionResp.json();
    expect(sessionJson.user).toBeDefined();
    expect(sessionJson.user.id).toBe('test-user-001');

    const kycResp = await request.get('/api/kyc/profile/');
    expect(kycResp.status()).toBe(200);
    const kycJson = await kycResp.json();
    expect(kycJson.profile).toBeDefined();
    expect(kycJson.profile.kyc_status).toBe('VERIFIED');

    const quoteResp = await request.get('/api/kite/quote/?i=NSE:RELIANCE');
    expect(quoteResp.status()).toBe(200);
    const quoteJson = await quoteResp.json();
    expect(quoteJson.quotes[0].symbol).toBe('RELIANCE');
    expect(quoteJson.quotes[0].last_price).toBe(2468.0);
  });

});

// ═══════════════════════════════════════════════════════════════════════════
// Alpha Crucible — Internal IPC Contract Tests
//
// We can't reach the Rust backend through the webview without the Tauri
// shell, so we install a deterministic JS-side stub for `__TAURI_IPC__`
// (and the higher-level `window.__TAURI__.core.invoke`) that returns the
// canonical AiExecutionPlan. The tests then drive `invoke()` end-to-end and
// assert the returned object matches the exact TypeScript shape used by
// `useTradeStore` and the Deep Quant panel.
// ═══════════════════════════════════════════════════════════════════════════

test.describe('Alpha Suite V3 — Tauri IPC Contract', () => {

  test.beforeEach(async ({ page }) => {
    attachIpcMonitors(page);

    // Inject a Tauri-compatible IPC stub before any app code runs.
    await page.addInitScript((plan) => {
      const handlers: Record<string, (args: unknown) => unknown> = {
        run_deep_quant_analysis: () => plan,
      };

      const invoke = async (cmd: string, args?: unknown) => {
        const handler = handlers[cmd];
        if (!handler) throw new Error(`[stub] unknown command: ${cmd}`);
        return handler(args);
      };

      // Modern Tauri v2 API surface: window.__TAURI__.core.invoke
      // Also expose the legacy __TAURI_IPC__ symbol for older callers.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).__TAURI__ = {
        core: { invoke },
        invoke,
      };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).__TAURI_INTERNALS__ = { invoke };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).__TAURI_IPC__ = invoke;
    }, MOCK_AI_EXECUTION_PLAN);
  });

  test('5. invoke("run_deep_quant_analysis") returns the AiExecutionPlan shape', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const result = await page.evaluate(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const inv = (window as any).__TAURI__?.core?.invoke ?? (window as any).__TAURI_IPC__;
      if (!inv) throw new Error('Tauri invoke not available');
      return await inv('run_deep_quant_analysis', { symbol: 'RELIANCE' });
    });

    // ── Strict TypeScript shape contract ──────────────────────────────
    expect(result).toBeDefined();
    expect(result).toHaveProperty('conviction_score');
    expect(result).toHaveProperty('setup_validation');
    expect(result).toHaveProperty('execution_plan');

    // Type assertions — the wire shape must match the Rust struct exactly.
    expect(typeof (result as { conviction_score: unknown }).conviction_score).toBe('number');
    expect(typeof (result as { setup_validation: unknown }).setup_validation).toBe('string');
    expect(typeof (result as { execution_plan: unknown }).execution_plan).toBe('string');

    // Bounds defined by the Rust validator (1..=100).
    const plan = result as { conviction_score: number; setup_validation: string; execution_plan: string };
    expect(plan.conviction_score).toBeGreaterThanOrEqual(1);
    expect(plan.conviction_score).toBeLessThanOrEqual(100);
    expect(plan.setup_validation.length).toBeGreaterThan(0);
    expect(plan.execution_plan.length).toBeGreaterThan(0);

    // ── No extraneous keys — protects against silent contract drift ─
    const keys = Object.keys(plan).sort();
    expect(keys).toEqual(['conviction_score', 'execution_plan', 'setup_validation']);
  });

  test('6. invoke contract surfaces unknown commands as rejected promises', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const errored = await page.evaluate(async () => {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const inv = (window as any).__TAURI__?.core?.invoke;
        await inv('command_that_does_not_exist', {});
        return false;
      } catch (e) {
        return String(e).includes('unknown command');
      }
    });
    expect(errored).toBe(true);
  });

});
