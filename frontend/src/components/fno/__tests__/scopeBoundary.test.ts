// Feature: fno-frontend-section — Scope-boundary verification (task 9.1)
//
// These are STRUCTURAL / scope tests (not property tests). They read the F4
// frontend module's source files from disk and assert on their CONTENTS to
// guard the consumption-only contract:
//
//   - R6.3 / R9.1: the FNO_Section consumes the F1/F2/F3 outputs and computes
//     NO options analytic in the frontend.
//   - R9.2: the FNO_Section places / modifies / commits NO trade — it is a
//     visualization workspace only.
//
// The challenge: the components legitimately REFERENCE the analytics as field
// NAMES for display (pcrOi, maxPain, ivSkew, walls, support, resistance, …) and
// the pure selectors (`buildOiProfile` / `buildIvSkew`) legitimately RESHAPE the
// snapshot for presentation (sort / filter / map / pass through, nearest-strike
// snapping via `Math.abs`). Those are display/passthrough, NOT analytics.
//
// So we do NOT grep for the metric names (that would false-positive on every
// display label). Instead we assert the ABSENCE of two concrete signatures of
// in-frontend computation:
//
//   (A) No import of any options-analytics module / recompute helper.
//   (B) No arithmetic that DERIVES a metric — e.g. dividing put OI by call OI to
//       compute a PCR, summation/reduce loops assigning to a pcr/maxPain/iv/skew
//       variable, or Black-Scholes IV math (Math.log/exp/sqrt/pow). To avoid
//       matching prose, every computation regex runs against a COMMENT-STRIPPED
//       copy of the source (the files document the contract heavily in comments).
//
// And for trades:
//
//   (C) Every `invoke(...)` target is one of the four allowed F&O bridge
//       commands, and no trade-execution command name appears anywhere.

import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// ---------------------------------------------------------------------------
// Source discovery: every .ts/.tsx file directly in the F4 module directory
// (`frontend/src/components/fno/`), excluding this `__tests__` folder.
// ---------------------------------------------------------------------------

const TESTS_DIR = path.dirname(fileURLToPath(import.meta.url));
const FNO_DIR = path.resolve(TESTS_DIR, '..');

interface SourceFile {
  name: string;
  /** Raw source, exactly as on disk. */
  raw: string;
  /** Source with block/line comments removed (for arithmetic-signature scans). */
  code: string;
}

/**
 * Strip block (`/* … *\/`) and line (`// …`) comments so the computation-signature
 * regexes match real code, not the heavy contract documentation in the comments
 * (which intentionally says "compute", "PCR", "max pain", etc.). This is a
 * deliberately simple stripper — sufficient because the subsequent scans look
 * for arithmetic/identifiers, not for `//` or `/*` sequences inside strings, and
 * none of the F&O sources embed those in string literals.
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ') // block comments
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1'); // line comments (keep e.g. "http://" intact)
}

function loadFnoSources(): SourceFile[] {
  const entries = readdirSync(FNO_DIR, { withFileTypes: true });
  return entries
    .filter((e) => e.isFile() && /\.(ts|tsx)$/.test(e.name))
    .map((e) => {
      const raw = readFileSync(path.join(FNO_DIR, e.name), 'utf8');
      return { name: e.name, raw, code: stripComments(raw) };
    });
}

const SOURCES = loadFnoSources();

// Sanity guard: if discovery ever returns nothing the absence-assertions below
// would vacuously pass, so fail loudly instead.
describe('F4 scope boundary — source discovery', () => {
  it('finds the F&O module source files', () => {
    const names = SOURCES.map((s) => s.name).sort();
    expect(names.length).toBeGreaterThan(0);
    // The module should at minimum contain its known files.
    expect(names).toEqual(
      expect.arrayContaining([
        'FnoSection.tsx',
        'OiProfileChart.tsx',
        'IvSkewChart.tsx',
        'OptionsHud.tsx',
        'FnoUnavailableState.tsx',
        'viewModel.ts',
      ]),
    );
  });
});

// ---------------------------------------------------------------------------
// (A) + (B): consumption-only — no analytics import, no analytics math (R6.3, R9.1)
// ---------------------------------------------------------------------------

describe('F4 scope boundary — computes no options analytic (R6.3, R9.1)', () => {
  // (A) Extract every import specifier (the module path in `from '…'`,
  // `import('…')`, or `require('…')`) and assert none point at an
  // options-analytics module or a recompute helper. The legitimate imports are
  // UI/runtime libs, the store, lightweight-charts, and sibling fno files.
  const importSpecifierRe =
    /(?:import|export)\s[^'"]*from\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)|import\(\s*['"]([^'"]+)['"]\s*\)/g;

  // A module path is suspect if it looks like it pulls in analytics computation.
  // (The fno dir is "fno", not "options"/"analytics", so any such import would
  // be reaching into a compute layer.)
  const ANALYTICS_IMPORT_RE = /(options[-_]?analytics|analytics[-_]?engine|compute[-_]?\w*analytic|options[_-]?bias|black[-_]?scholes|greeks|\bmaxpain\b)/i;

  it('imports no options-analytics module or recompute helper', () => {
    const offenders: Array<{ file: string; specifier: string }> = [];

    for (const src of SOURCES) {
      let m: RegExpExecArray | null;
      importSpecifierRe.lastIndex = 0;
      while ((m = importSpecifierRe.exec(src.code)) !== null) {
        const specifier = m[1] ?? m[2] ?? m[3];
        if (specifier && ANALYTICS_IMPORT_RE.test(specifier)) {
          offenders.push({ file: src.name, specifier });
        }
      }
    }

    expect(offenders, `Unexpected analytics imports: ${JSON.stringify(offenders)}`).toEqual([]);
  });

  // (B) Arithmetic-signature scan. Each entry is an "obvious computation
  // signature" that would only appear if the frontend DERIVED a metric. They
  // are written to NOT match the legitimate passthrough selectors, which assign
  // metrics with a colon (`pcrOi: finiteOrNull(...)`, `maxPain: ...`) and only
  // sort/filter/map/Math.abs the chain for presentation.
  const COMPUTATION_SIGNATURES: Array<{ label: string; re: RegExp }> = [
    {
      // PCR derived by dividing put OI by call OI, in any common spelling.
      label: 'PCR derived by dividing put OI by call OI',
      re: /\b(pe_oi|put_?oi|putOi|putOpenInterest)\b\s*\/\s*\b(ce_oi|call_?oi|callOi|callOpenInterest)\b/i,
    },
    {
      // A pcr/maxPain/iv/skew VARIABLE assigned (`=`, not `:`) from a reduce.
      label: 'metric variable assigned from a reduce()',
      re: /\b(pcr\w*|max_?pain|implied_?vol\w*|iv_?skew|skew\w*|oi_?wall\w*)\b\s*=\s*[^;:=][^;\n]*\.reduce\s*\(/i,
    },
    {
      // A summation loop accumulating into a pcr/maxPain/iv/skew accumulator.
      label: 'metric accumulator built with += in a loop',
      re: /\b(pcr\w*|max_?pain|implied_?vol\w*|iv_?skew|skew\w*)\b\s*\+=\s*/i,
    },
    {
      // Black-Scholes / implied-volatility math signatures. The selectors only
      // use Math.abs/round/floor for presentation; log/exp/sqrt/pow would mean
      // the UI is computing IV/Greeks itself.
      label: 'Black-Scholes / IV math (Math.log|exp|sqrt|pow)',
      re: /Math\.(log|exp|sqrt|pow)\s*\(/,
    },
    {
      // Named recompute helpers — the UI must consume, not recompute.
      label: 'recompute helper (computePcr/computeMaxPain/computeIv/…)',
      re: /\b(compute|calc|calculate|derive|recompute)(Pcr|MaxPain|Iv|Skew|Greeks|OiWalls?|Analytics|Bias|FuturesBasis)\w*/i,
    },
  ];

  it.each(COMPUTATION_SIGNATURES)(
    'contains no computation signature: $label',
    ({ re }) => {
      const offenders: Array<{ file: string; match: string }> = [];
      for (const src of SOURCES) {
        const m = src.code.match(re);
        if (m) {
          offenders.push({ file: src.name, match: m[0] });
        }
      }
      expect(
        offenders,
        `Found in-frontend analytics computation: ${JSON.stringify(offenders)}`,
      ).toEqual([]);
    },
  );

  // Positive control: the comment-stripping + passthrough shape must NOT trip
  // the scans. `buildOiProfile`/`buildIvSkew` legitimately reference the metric
  // names with a colon and reshape via sort/filter/map — assert those exist so
  // the test is exercising real passthrough code (guards against a future
  // refactor that silently removes the selectors and makes the scans vacuous).
  it('still recognizes the legitimate passthrough selectors (not false-positived)', () => {
    const vm = SOURCES.find((s) => s.name === 'viewModel.ts');
    expect(vm).toBeDefined();
    expect(vm!.code).toMatch(/maxPain:\s*finiteOrNull\(/); // passthrough, not compute
    expect(vm!.code).toMatch(/pcrOi:\s*finiteOrNull\(/);
    expect(vm!.code).toMatch(/\.sort\(/); // presentation reshape only
  });
});

// ---------------------------------------------------------------------------
// (C): places no trade — invokes only the F&O bridge commands (R9.2)
// ---------------------------------------------------------------------------

describe('F4 scope boundary — invokes no trade-execution command (R9.2)', () => {
  // The only Tauri commands the section may invoke (the F&O transport bridge).
  const ALLOWED_INVOKE_COMMANDS = new Set([
    'fno_list_chains',
    'get_fno_analytics',
    'fno_subscribe',
    'fno_unsubscribe',
  ]);

  // Matches `invoke('cmd'`, `invoke("cmd"`, and `invoke<T>('cmd'` (Tauri's
  // generic form), capturing the command name.
  const invokeCallRe = /\binvoke\s*(?:<[^>]*>)?\s*\(\s*['"]([^'"]+)['"]/g;

  it('only invokes the four allowed F&O bridge commands', () => {
    const invoked: Array<{ file: string; command: string }> = [];

    for (const src of SOURCES) {
      let m: RegExpExecArray | null;
      invokeCallRe.lastIndex = 0;
      while ((m = invokeCallRe.exec(src.raw)) !== null) {
        invoked.push({ file: src.name, command: m[1] });
      }
    }

    const disallowed = invoked.filter((i) => !ALLOWED_INVOKE_COMMANDS.has(i.command));
    expect(
      disallowed,
      `Disallowed invoke() targets (only F&O bridge commands are permitted): ${JSON.stringify(
        disallowed,
      )}`,
    ).toEqual([]);
  });

  // Explicit denylist: assert no known trade-execution command name appears
  // anywhere in the module source (paper or real). This catches a trade command
  // passed indirectly (not as a string literal directly inside `invoke(`).
  const TRADE_EXECUTION_COMMANDS = [
    'execute_paper_trade',
    'execute_real_trade',
    'execute_trade',
    'place_order',
    'place_trade',
    'submit_order',
    'cancel_order',
    'modify_order',
    'close_position',
    'square_off',
    'paper_trade',
    'real_trade',
  ];

  it.each(TRADE_EXECUTION_COMMANDS)(
    'never references the trade-execution command "%s"',
    (command) => {
      const offenders = SOURCES.filter((src) => src.raw.includes(command)).map((s) => s.name);
      expect(
        offenders,
        `Trade-execution command "${command}" must not appear in the F&O frontend module`,
      ).toEqual([]);
    },
  );
});
