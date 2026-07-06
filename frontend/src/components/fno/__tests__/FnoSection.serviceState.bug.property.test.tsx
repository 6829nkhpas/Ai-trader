// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: fno-data-and-search-fix (bugfix)
//
// Property 1 (Bug Condition), frontend-render + Rust-transport seam —
// "F&O Data Flows or Fails With a Specific, Honest Reason":
//
//   For all requests satisfying isBugCondition_A with
//   service_state IN { unreachable, misconfigured } — a down/misconfigured F&O
//   service so the bridge `fetch_snapshot_from` (commands/fno.rs) returns
//   Err(String) naming the URL, surfaced as a rejected `get_fno_analytics`
//   invoke — the FnoSection state derivation SHALL classify the input into a
//   DISTINCT `service_unreachable`/`misconfigured` render state, SEPARATE from a
//   resolved no-data marker, and never throw.
//
//   Validates: Requirements 1.2, 2.3
//
// *** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***
//
// On unfixed code the FnoSection renders a transport `Err` and a resolved
// `unavailable: true` marker IDENTICALLY: both collapse to the single generic
// `FnoUnavailableState` ("F&O Data Unavailable"), differing only in the human
// reason text. There is no distinct, actionable service/configuration state, so
// a fixable setup problem (service down / FNO_SERVICE_URL misconfigured) is
// shown to the user as an honest empty market.
//
// The distinctness the fix (task 3.4) must add is an actionable service/config
// render state — per the design, "service unreachable / check FNO_SERVICE_URL".
// This test asserts the transport-error scenario surfaces that actionable
// config signal (which the unfixed generic reason string never contains) while
// the resolved no-data marker does NOT — proving the two are classified
// distinctly. DO NOT fix the test or the code here; task 3.5 re-runs it.
//
// These tests exercise the REAL FnoSection against MOCKED Tauri IPC seams; only
// the heavy chart children are stubbed. FnoUnavailableState is left REAL so its
// rendered copy is observable.

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import fc from 'fast-check';

import type { FnoUnavailableMarker } from '../viewModel';

// ── Configurable Tauri IPC seams (hoisted so mock factories can bind them) ────
const tauri = vi.hoisted(() => ({
  invokeMock: null as any,
  listenMock: null as any,
}));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: any[]) => tauri.invokeMock(...args),
}));

vi.mock('@tauri-apps/api/event', () => ({
  listen: (...args: any[]) => tauri.listenMock(...args),
}));

// Stub the heavy chart children (canvas/recharts); keep FnoUnavailableState REAL.
vi.mock('../OiProfileChart', () => ({
  __esModule: true,
  default: () => React.createElement('div', { 'data-testid': 'oi-chart' }),
}));
vi.mock('../IvSkewChart', () => ({
  __esModule: true,
  default: () => React.createElement('div', { 'data-testid': 'iv-chart' }),
}));
vi.mock('../OptionsHud', () => ({
  __esModule: true,
  default: () => React.createElement('div', { 'data-testid': 'options-hud' }),
  OptionsHud: () => React.createElement('div', { 'data-testid': 'options-hud' }),
}));

// react-resizable-panels renders fine in jsdom, but stub to keep it lightweight.
vi.mock('react-resizable-panels', () => ({
  __esModule: true,
  Group: ({ children }: any) => React.createElement('div', null, children),
  Panel: ({ children }: any) => React.createElement('div', null, children),
  Separator: () => React.createElement('div', null),
}));

import FnoSection from '../FnoSection';
import { useTradeStore } from '@/store/useTradeStore';

const FNO_SERVICE_BASE = 'http://localhost:8086';
const CONFIGURED_CHAINS = {
  underlyings: ['NIFTY 50'],
  expiries_by_underlying: { 'NIFTY 50': ['2024-12-26'] },
};

/** The transport-error string fetch_snapshot_from returns, verbatim (fno.rs). */
function transportErrorString(base: string, serviceState: 'unreachable' | 'misconfigured'): string {
  const detail =
    serviceState === 'unreachable'
      ? 'error sending request for url: connection refused'
      : 'dns error: failed to lookup address information';
  return `F&O service unreachable at ${base}/options/snapshot: ${detail}`;
}

/** A resolved no-data Unavailable_Marker (genuine empty market, not a bug). */
function noDataMarker(reason: string, lastSnapshotTs: number | null): FnoUnavailableMarker {
  const marker: FnoUnavailableMarker = {
    underlying: 'NIFTY 50',
    expiry: '2024-12-26',
    unavailable: true,
    reason,
  };
  if (lastSnapshotTs !== null) marker.last_snapshot_ts = lastSnapshotTs;
  return marker;
}

/**
 * Install an `invoke` mock whose `get_fno_analytics` behaves per `snapshot`:
 *   - a string -> the promise REJECTS with it (transport Err; service down)
 *   - an object -> the promise RESOLVES with it (a resolved marker/payload)
 * Everything else (`fno_list_chains`, `fno_subscribe`, `fno_unsubscribe`)
 * resolves benignly so the section mounts cleanly.
 */
function installInvoke(snapshot: string | object) {
  tauri.invokeMock = vi.fn(async (cmd: string) => {
    if (cmd === 'fno_list_chains') return CONFIGURED_CHAINS;
    if (cmd === 'get_fno_analytics') {
      if (typeof snapshot === 'string') throw snapshot;
      return snapshot;
    }
    return undefined; // fno_subscribe / fno_unsubscribe
  });
}

beforeEach(() => {
  // Mount FnoSection for the configured underlying (as the F&O workspace would).
  useTradeStore.setState({ fnoUnderlying: 'NIFTY 50', fnoExpiry: '' });
  // listen() resolves to a no-op unlisten fn.
  tauri.listenMock = vi.fn(async () => () => {});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Render FnoSection in an isolated DOM and return its settled text. */
async function renderAndReadPanelText(): Promise<string> {
  cleanup(); // guarantee a clean DOM for this iteration
  const { container } = render(React.createElement(FnoSection));
  // Wait until the section settles out of its loading state (the unavailable /
  // service panel has resolved).
  await waitFor(() =>
    expect(screen.queryByText(/Loading F&O analytics/i)).not.toBeInTheDocument(),
  );
  return container.textContent ?? '';
}

/** True when the rendered copy carries the actionable service/config signal. */
function hasServiceConfigSignal(text: string): boolean {
  // The fix's distinct service state must be actionable — per design 3.4 it
  // tells the user to "check FNO_SERVICE_URL". This literal env-var name (and
  // an explicit configuration/misconfigured framing) is NEVER present in the
  // unfixed generic reason string, so it is a stable discriminator.
  return /FNO_SERVICE_URL/i.test(text) || /misconfigur|configuration problem/i.test(text);
}

describe('Defect A (service unreachable): distinct render state — EXPECTED FAIL on unfixed code', () => {
  it('a transport Err (service down/misconfigured) renders a DISTINCT actionable service/config state', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom<'unreachable' | 'misconfigured'>('unreachable', 'misconfigured'),
        fc.constantFrom(FNO_SERVICE_BASE, 'http://127.0.0.1:9999', 'http://wrong-host:1234'),
        async (serviceState, base) => {
          installInvoke(transportErrorString(base, serviceState));

          // never throws: rendering the error path must not crash the UI.
          const text = await renderAndReadPanelText();

          // EXPECTED FAIL on unfixed code: the transport error is rendered as
          // the generic no-data panel, so no actionable service/config signal
          // is present.
          expect(hasServiceConfigSignal(text)).toBe(true);
        },
      ),
      { numRuns: 6 },
    );
  });

  it('a resolved no-data marker renders the generic no-data state WITHOUT any service/config signal (distinctness baseline)', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom(
          'no chain snapshot available for NIFTY 50',
          'no chain snapshot available for NIFTY 50 / 2024-12-26',
          'market closed',
        ),
        fc.option(fc.integer({ min: 1_600_000_000_000, max: 2_000_000_000_000 }), { nil: null }),
        async (reason, lastTs) => {
          installInvoke(noDataMarker(reason, lastTs));

          const text = await renderAndReadPanelText();

          // A genuine no-data marker is NOT a service/config problem: it must
          // never surface the actionable service-config signal. (Holds today
          // and must keep holding — this is the distinctness counterpart.)
          expect(hasServiceConfigSignal(text)).toBe(false);
        },
      ),
      { numRuns: 6 },
    );
  });
});
