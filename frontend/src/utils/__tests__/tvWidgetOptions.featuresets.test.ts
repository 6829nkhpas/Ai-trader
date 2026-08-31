// What the vendored charting library will and will not accept.
//
// TradingView IGNORES an unknown featureset silently. No warning, no error — the
// feature simply never appears, which is indistinguishable from the library not
// supporting it. That makes a wrong name here one of the more expensive mistakes
// available: you go hunting through CSS, container sizes and widget editions
// before suspecting the string.
//
// The names are therefore checked against the vendored `charting_library.d.ts`,
// which declares the authoritative unions. Reading the real type definition rather
// than a copy means this cannot drift on a library upgrade.
//
// The finding that prompted this file: the 1–8 pane layout grid (`header_layouttoggle`
// / `support_multicharts`) is gated by featuresets in `TradingTerminalFeatureset`
// — the Trading Platform edition — while we vendor Advanced Charts. It LOOKS
// available, because `setLayout`, `chartsCount` and `MultipleChartsLayoutType`'s 40
// arrangements are declared in the shared type definitions and the "Select layout"
// / "Sync in layout" strings ship in `bundles/library.*.js`. The type definitions
// and localisation assets are shared across editions; the featureset unions are
// what actually differ.
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { getTvWidgetOptions } from '../tvWidgetOptions';

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const LIB_DTS = path.join(
  FRONTEND,
  'public/static/charting_library/charting_library/charting_library.d.ts',
);

/** Every quoted name inside one `export type X = "a" | "b" | …;` union. */
function union(name: string): Set<string> {
  const src = readFileSync(LIB_DTS, 'utf8');
  const start = src.indexOf(`export type ${name} =`);
  expect(start, `${name} not found — did the library's type layout change?`).toBeGreaterThan(-1);
  const end = src.indexOf(';', start);
  expect(end).toBeGreaterThan(start);
  return new Set([...src.slice(start, end).matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]));
}

function passedFeaturesets(): string[] {
  const opts = getTvWidgetOptions({
    container: {} as HTMLDivElement,
    datafeed: {},
    activeSymbol: 'RELIANCE',
    resolution: '10',
    theme: 'dark',
  });
  return [...(opts.enabled_features ?? []), ...(opts.disabled_features ?? [])];
}

/**
 * Names we pass that this build's base union does not declare.
 *
 * Pinned rather than asserted empty, deliberately. These predate this test and
 * fall into two groups worth telling apart before touching them:
 *
 *   · `load_last_chart` is long-standing and widely documented; the union may
 *     simply not enumerate every legacy name, and the app relies on it for chart
 *     persistence. Removing it on the strength of this parse would be a guess.
 *   · the `chart_style_*` entries other than `hilo` name chart types (Renko, Kagi,
 *     P&F, Line Break, TPO, SVP, volume candles, volume footprint) that this build
 *     declares no featureset for at all — only `chart_style_hilo` and
 *     `chart_style_hilo_last_price` exist. Those are very likely doing nothing.
 *
 * Both need checking against the running widget, not a `.d.ts`. Pinning the set
 * means a NEW unknown name fails immediately while these stay visible as a
 * question rather than being silently blessed.
 */
const KNOWN_UNDECLARED = new Set([
  'load_last_chart',
  'chart_style_range',
  'chart_style_renko',
  'chart_style_kagi',
  'chart_style_pnf',
  'chart_style_line_break',
  'chart_style_vol_footprint',
  'chart_style_tpo',
  'chart_style_svp',
  'chart_style_vol_candle',
]);

describe('the charting library featuresets we pass', () => {
  it('has the vendored library available to check against', () => {
    // The library is a submodule seeded outside the repo on the droplet, so a
    // machine without it must fail loudly rather than pass vacuously.
    expect(existsSync(LIB_DTS), `expected the charting library at ${LIB_DTS}`).toBe(true);
    expect(union('ChartingLibraryFeatureset').size).toBeGreaterThan(100);
    expect(passedFeaturesets().length).toBeGreaterThan(10);
  });

  it('introduces no new name the library does not declare', () => {
    const declared = union('ChartingLibraryFeatureset');
    const unknown = passedFeaturesets().filter(
      (f) => !declared.has(f) && !KNOWN_UNDECLARED.has(f),
    );
    expect(
      unknown,
      'ignored silently by TradingView, so whatever it was meant to enable never appears',
    ).toEqual([]);
  });

  it('still passes every name in the pinned undeclared set, or drops it from the pin', () => {
    // Keeps the pin honest: if one of those entries is removed from the config, it
    // must be removed from `KNOWN_UNDECLARED` too, so the list cannot rot into a
    // permanent excuse.
    const passed = new Set(passedFeaturesets());
    const stale = [...KNOWN_UNDECLARED].filter((f) => !passed.has(f));
    expect(stale, 'no longer passed — delete these from KNOWN_UNDECLARED').toEqual([]);
  });

  it('passes no Trading-Platform-only featureset to this Advanced Charts build', () => {
    const terminalOnly = union('TradingTerminalFeatureset');
    const base = union('ChartingLibraryFeatureset');

    // The premise: these two really are edition-gated, not merely missing.
    expect(terminalOnly.has('header_layouttoggle')).toBe(true);
    expect(terminalOnly.has('support_multicharts')).toBe(true);
    expect(base.has('header_layouttoggle')).toBe(false);
    expect(base.has('support_multicharts')).toBe(false);

    // So passing them would be a no-op that reads like the layout grid is enabled.
    const passed = passedFeaturesets();
    expect(passed).not.toContain('header_layouttoggle');
    expect(passed).not.toContain('support_multicharts');
  });

  it('records that the layout API is present but edition-gated', () => {
    // Written down because this is exactly what makes the feature look reachable.
    // If a future library drop moves these into the base union, the assertion above
    // flips and the grid becomes a two-line change.
    const src = readFileSync(LIB_DTS, 'utf8');
    expect(src).toContain('chartsCount(): number');
    expect(src).toContain('setLayout(layout: LayoutType): void');
    const multi = src.slice(src.indexOf('export type MultipleChartsLayoutType'), 1000 + src.indexOf('export type MultipleChartsLayoutType'));
    for (const layout of ['"2h"', '"2v"', '"3s"', '"2-2"', '"4s"', '"5h"', '"6c"', '"8v"']) {
      expect(multi, `layout ${layout} missing`).toContain(layout);
    }
  });
});
