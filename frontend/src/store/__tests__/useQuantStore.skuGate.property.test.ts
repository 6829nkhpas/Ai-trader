// @vitest-environment node
/* eslint-disable @typescript-eslint/no-explicit-any */

// Compliance blocker P1 — runtime gate proof.
//
// `lib/__tests__/sku.property.test.ts` proves the SKU *model* refuses. This file
// proves the *call sites* honour it: that an unentitled (TERMINAL) user driving
// the real `useQuantStore` actions issues NO backend call at all, on either
// transport (Tauri IPC on desktop, HTTP on the website).
//
// "No call" is the property that matters and the reason this is a separate test.
// A gate that renders a locked placeholder while still firing
// `run_deep_quant_agent` has not withheld the regulated output — it has merely
// hidden it, and the recommendation was still generated, billed and logged. So
// the assertion here is on the mocked transport spy, not the store's error text.
//
// This layer is defence in depth. The authoritative gate is server-side in
// `agents/deep-quant-loop/entitlements.py`, which the user cannot patch out.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fc from 'fast-check';

// ── Environment prerequisites, set before ANY import is evaluated ─────────
// `useQuantStore` → `useAuthStore` → `lib/env.ts`, which THROWS when
// `NEXT_PUBLIC_API_BASE_URL` is unset. Vitest does not load `.env.local` into
// `process.env`, so without this the suite cannot even collect. A `vi.hoisted`
// block runs before the import bindings are evaluated, which a top-level
// statement would not.
//
// Deliberately self-contained: this is a Gate 0→1 compliance artefact and must
// run in CI on a bare checkout, not only on a developer machine that happens to
// have a populated `.env.local`.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://127.0.0.1:0/api/v1';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});

// ── Transport mocks, installed before the store module is imported ────────
// `useQuantStore` reaches the backend through `lib/bridge`, which is the single
// transport chokepoint: `invoke()` under Tauri, HTTP in a browser. Mocking THERE
// rather than at `@tauri-apps/api/core` makes this property transport-agnostic —
// the gate must withhold the regulated output on the website too, not only on
// desktop. `importOriginal` keeps the real `isTauri` / `BridgeUnsupportedError`
// so nothing else in the module graph loses its behaviour.
//
// The spies live in `vi.hoisted` because `lib/bridge` is imported *eagerly* here
// (`lib/tauriFetch.ts` pulls it in), so the factory runs during import evaluation
// — before a plain `const` at module scope would be initialized. The old
// `@tauri-apps/api/core` mock got away with a plain `const` only because that
// module was reached through a lazy `await import`.

const { invokeSpy, listenSpy } = vi.hoisted(() => ({
  invokeSpy: vi.fn(async () => ({})),
  listenSpy: vi.fn(async () => () => {}),
}));

vi.mock('@/lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/bridge')>()),
  bridgeInvoke: invokeSpy,
  bridgeListen: listenSpy,
}));

import { useQuantStore } from '@/store/useQuantStore';
import { useFeatureStore } from '@/store/useFeatureStore';
import { AGENT_MODES, AGENT_MODE_SKU, RESEARCH_LOCKED_MESSAGE } from '@/lib/sku';
import type { AccessFlags } from '@/lib/api/types';

const RESEARCH_MODES = AGENT_MODES.filter((m) => AGENT_MODE_SKU[m] === 'RESEARCH');

/** The two agent modes `fetchDeepAnalysis` actually accepts. */
const RUNNABLE_MODES = ['FIND', 'VERIFY'] as const;

const TERMINAL_FLAGS = { canAccessResearch: false } as unknown as AccessFlags;
const RESEARCH_FLAGS = { canAccessResearch: true } as unknown as AccessFlags;

function setSku(flags: AccessFlags | null) {
  useFeatureStore.getState().setAccessFlags(flags);
}

describe('P1 — useQuantStore issues no IPC for an unentitled user', () => {
  beforeEach(() => {
    // Enforcement must be ON for these assertions to mean anything; with it off
    // the gate deliberately no-ops so local development is unaffected.
    vi.stubEnv('NEXT_PUBLIC_SKU_ENFORCE', 'true');
    invokeSpy.mockClear();
    listenSpy.mockClear();
    useFeatureStore.getState().reset();
    useQuantStore.setState({
      sessionsByKey: {},
      activeViewKey: null,
      qaMessages: [],
      qaStatus: 'idle',
      currentThreadId: null,
    } as any);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('sanity: the mock is wired, so a zero-call assertion is meaningful', async () => {
    // Without this, every "no IPC" test below would pass trivially if the mock
    // path were wrong. Prove the spy DOES fire on the entitled path.
    setSku(RESEARCH_FLAGS);
    expect(useFeatureStore.getState().sku).toBe('RESEARCH');
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');
    expect(invokeSpy).toHaveBeenCalled();
  });

  it('defaults to TERMINAL before any entitlement data arrives', () => {
    expect(useFeatureStore.getState().sku).toBe('TERMINAL');
    expect(useFeatureStore.getState().hydrated).toBe(false);
  });

  it('issues no IPC for a FIND run under TERMINAL', async () => {
    setSku(TERMINAL_FLAGS);
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');
    expect(invokeSpy).not.toHaveBeenCalled();
  });

  it('issues no IPC for a FIND run when entitlement data never arrived', async () => {
    // The store was reset in beforeEach and never hydrated — the most likely
    // real-world state during a cold start or a failed /credit call.
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');
    expect(invokeSpy).not.toHaveBeenCalled();
  });

  it('issues no IPC for a FIND run when the flag is a truthy string', async () => {
    // A loosely-typed remote response must not become an entitlement.
    setSku({ canAccessResearch: 'true' } as unknown as AccessFlags);
    expect(useFeatureStore.getState().sku).toBe('TERMINAL');
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');
    expect(invokeSpy).not.toHaveBeenCalled();
  });

  it('surfaces the refusal as a session error rather than a silent no-op', async () => {
    setSku(TERMINAL_FLAGS);
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');

    const state = useQuantStore.getState();
    const key = state.activeViewKey;
    expect(key).toBeTruthy();
    const session = state.sessionsByKey[key as string];
    expect(session).toBeDefined();
    expect(session.sessionStatus).toBe('error');
    expect(session.isAnalyzing).toBe(false);
    expect(session.analysisError).toBe(RESEARCH_LOCKED_MESSAGE);
  });

  it('leaves the VERIFY path fully working under TERMINAL', async () => {
    // The whole point of the repackage: nothing is removed. VERIFY validates the
    // user's own entry/stop/target, which is not regulated research, so it must
    // still reach the backend on the unregulated SKU.
    setSku(TERMINAL_FLAGS);
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'VERIFY', {
      side: 'LONG',
      entry: 1400,
      stopLoss: 1380,
      takeProfit: 1450,
      userAnalysis: 'range breakout',
    });
    expect(invokeSpy).toHaveBeenCalled();
  });

  it('issues no IPC for Q&A under TERMINAL, and renders the refusal in the transcript', async () => {
    setSku(TERMINAL_FLAGS);
    useQuantStore.setState({ currentThreadId: 'thread-abc' } as any);

    await useQuantStore.getState().askQuestion('what is the target on this setup');

    expect(invokeSpy).not.toHaveBeenCalled();
    const msgs = useQuantStore.getState().qaMessages;
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe('user');
    expect(msgs[1].role).toBe('assistant');
    expect(msgs[1].content).toBe(RESEARCH_LOCKED_MESSAGE);
    expect(msgs[1].error).toBe(true);
    // The transcript must not be left mid-stream — a refusal is a finished turn.
    expect(useQuantStore.getState().qaStatus).not.toBe('streaming');
  });

  it('holds for every RESEARCH mode reachable from the store, over arbitrary symbols', async () => {
    // Property form: no symbol string, and no RESEARCH mode, produces IPC.
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom(...RUNNABLE_MODES.filter((m) => (RESEARCH_MODES as readonly string[]).includes(m))),
        fc.string({ minLength: 1, maxLength: 20 }),
        async (mode, symbol) => {
          invokeSpy.mockClear();
          useQuantStore.setState({ sessionsByKey: {}, activeViewKey: null } as any);
          setSku(TERMINAL_FLAGS);
          await useQuantStore.getState().fetchDeepAnalysis(symbol, mode as 'FIND' | 'VERIFY');
          expect(invokeSpy).not.toHaveBeenCalled();
        },
      ),
      { numRuns: 40 },
    );
  });

  it('re-locks after logout, so a stale entitlement cannot outlive the session', async () => {
    setSku(RESEARCH_FLAGS);
    expect(useFeatureStore.getState().sku).toBe('RESEARCH');

    useFeatureStore.getState().reset(); // what `useAuthStore.logout()` triggers
    expect(useFeatureStore.getState().sku).toBe('TERMINAL');

    invokeSpy.mockClear();
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');
    expect(invokeSpy).not.toHaveBeenCalled();
  });
});

describe('P1 — enforcement switch', () => {
  beforeEach(() => {
    invokeSpy.mockClear();
    useFeatureStore.getState().reset();
    useQuantStore.setState({ sessionsByKey: {}, activeViewKey: null } as any);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('does not gate when enforcement is off — dev must be unaffected', async () => {
    vi.stubEnv('NEXT_PUBLIC_SKU_ENFORCE', '');
    vi.stubEnv('NEXT_PUBLIC_PROD', '');
    setSku(TERMINAL_FLAGS);
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');
    expect(invokeSpy).toHaveBeenCalled();
  });

  it('gates automatically in a production build, with no extra flag needed', async () => {
    // A shipped build must not depend on someone remembering to set
    // NEXT_PUBLIC_SKU_ENFORCE. IS_PROD alone turns the gate on.
    vi.stubEnv('NEXT_PUBLIC_SKU_ENFORCE', '');
    vi.stubEnv('NEXT_PUBLIC_PROD', 'true');
    setSku(TERMINAL_FLAGS);
    await useQuantStore.getState().fetchDeepAnalysis('RELIANCE', 'FIND');
    expect(invokeSpy).not.toHaveBeenCalled();
  });
});
