// Guards two properties of the feature kill switches that only exist after
// bundling, and so cannot be caught by calling the code.
//
// PROPERTY 1 — the switch values must not reach the browser at all.
// Next.js implements a `NEXT_PUBLIC_*` var as a build-time textual substitution
// into the JS bundle. A kill switch read that way is shipped to the client, where
// it is both readable and editable in devtools. The switches therefore moved to
// unprefixed `ENABLE_*` names read on the server (`app/api/_featureSwitches.ts`)
// and in the Rust shell (`src-tauri/src/commands/features.rs`). Reintroducing a
// `NEXT_PUBLIC_ENABLE_*` read anywhere would silently undo that, so this asserts
// the name appears nowhere in the client tree.
//
// PROPERTY 2 — no client module may read env through a computed lookup.
// `process.env[key]` is not statically analysable, so Next leaves it untouched;
// in a browser `process` then resolves to the `process/browser` polyfill whose
// `env` is `{}` and the read yields `undefined`. This is not hypothetical:
// `featureFlags.ts` shipped with `envFlag(key) { return process.env[key] === 'true' }`,
// which compiled to `function e4(e){return"true"===y.default.env[e]}` in the
// production client chunk. All seven premium switches read `undefined`, so
// `computeFeatureAccess` returned `false` for every feature in production — in
// the shipped desktop webview as much as on the website. Dev never showed it,
// because the computation short-circuits to all-unlocked when not enforcing.
//
// A unit test cannot catch either property by calling the code: under Vitest
// `process.env` is the real Node object, so both a dynamic access and a
// `NEXT_PUBLIC_` read work perfectly. So these assert on the SOURCE TEXT, which
// is the thing the bundler actually keys on.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  ALL_SWITCHES_OFF,
  FEATURE_IDS,
  UNRESOLVED_FEATURE_CONFIG,
  computeFeatureAccess,
  parseFeatureConfig,
} from '../featureFlags';

const SRC_ROOT = join(__dirname, '..', '..');

function read(relative: string): string {
  return readFileSync(join(SRC_ROOT, relative), 'utf8');
}

/**
 * Strip comments so prose *describing* the anti-pattern is not mistaken for it.
 *
 * Deliberately crude: block comments, then `//` to end-of-line except in `://`
 * so URLs survive. That is enough for locating `process.env` accesses, and a
 * comment-aware parse would be more machinery than the property needs.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/** Every `process.env` access in `source`, paired with 'static' | 'dynamic'. */
function classifyEnvAccesses(source: string): Array<{ text: string; kind: 'static' | 'dynamic' }> {
  const out: Array<{ text: string; kind: 'static' | 'dynamic' }> = [];
  // `process.env` followed by either `.IDENT` (static) or `[` (computed).
  const re = /process\s*\.\s*env\s*(\.\s*[A-Za-z_$][\w$]*|\[)/g;
  for (const m of stripComments(source).matchAll(re)) {
    out.push({ text: m[0], kind: m[1] === '[' ? 'dynamic' : 'static' });
  }
  return out;
}

const SERVER_SWITCH_ENV = [
  'ENABLE_DEEPSEEK_GLM',
  'ENABLE_MULTI_MODEL',
  'ENABLE_GHOSTLINE',
  'ENABLE_FOOTPRINT',
  'ENABLE_TOPUP',
  'ENABLE_INSTANT_NEWS',
  'ENABLE_ADVANCE_CHART',
];

describe('kill switches are resolved server-side', () => {
  it('reads every switch in app/api/_featureSwitches.ts, unprefixed', () => {
    const source = read('app/api/_featureSwitches.ts');
    for (const key of SERVER_SWITCH_ENV) {
      expect(source, `${key} must be read as \`process.env.${key}\``).toContain(
        `process.env.${key}`,
      );
    }
    // The enforcement master switch, server-side counterpart of NEXT_PUBLIC_PROD.
    expect(source).toContain('process.env.FEATURE_ENFORCEMENT');
  });

  it('has one server switch per feature id', () => {
    const source = stripComments(read('app/api/_featureSwitches.ts'));
    const switches = source.match(/process\.env\.ENABLE_[A-Z_]+/g) ?? [];
    expect(new Set(switches).size).toBe(FEATURE_IDS.length);
  });

  it('never names a switch with the NEXT_PUBLIC_ prefix, which would inline it', () => {
    const source = read('app/api/_featureSwitches.ts');
    expect(stripComments(source)).not.toMatch(/NEXT_PUBLIC_ENABLE_/);
  });

  it('leaves lib/featureFlags.ts reading no environment at all', () => {
    // Stronger than "reads it statically": the pure module cannot pick up a
    // build-time constant if it never touches `process` in the first place.
    expect(classifyEnvAccesses(read('lib/featureFlags.ts'))).toEqual([]);
  });
});

describe('client modules never read env dynamically', () => {
  // The computed-lookup trap applies to any module that reaches the browser.
  // These are the client-side env readers; the server-only proxy handlers under
  // `app/api/` are excluded — they run in Node, where a computed read is fine.
  const CLIENT_ENV_READERS = [
    'lib/featureFlags.ts',
    'lib/env.ts',
    'store/useTradeStore.ts',
    'store/useAuthStore.ts',
    // `app/page.tsx` reads the four NEXT_PUBLIC_*_WS_URL values to open the live
    // sockets — inlined at build time, so a computed read would silently yield
    // undefined and the feeds would go quiet with no error.
    'app/page.tsx',
  ];

  for (const relative of CLIENT_ENV_READERS) {
    it(`${relative} reads env only via static member expressions`, () => {
      const dynamic = classifyEnvAccesses(read(relative)).filter((a) => a.kind === 'dynamic');
      expect(dynamic).toEqual([]);
    });
  }
});

describe('computeFeatureAccess', () => {
  it('locks everything under the unresolved default, whatever the plan says', () => {
    // The fail-closed direction: before the backend answers, a fully entitled
    // user still sees locked UI rather than a flash of premium panels.
    const entitled = Object.fromEntries(
      FEATURE_IDS.map((id) => [id, true]),
    ) as unknown as Parameters<typeof computeFeatureAccess>[0];
    const map = computeFeatureAccess(entitled, UNRESOLVED_FEATURE_CONFIG);
    for (const id of FEATURE_IDS) {
      expect(map[id], `${id} must stay locked until the config is known`).toBe(false);
    }
  });

  it('unlocks everything in a non-enforcing deployment regardless of accessFlags', () => {
    const map = computeFeatureAccess(null, { enforced: false, switches: ALL_SWITCHES_OFF });
    for (const id of FEATURE_IDS) {
      expect(map[id], `${id} should be unlocked in dev`).toBe(true);
    }
  });

  it('requires both the deployment switch and the plan flag when enforcing', () => {
    const config = {
      enforced: true,
      switches: { ...ALL_SWITCHES_OFF, footprint: true, ghostline: true },
    };
    // Plan grants footprint only; the deployment enables footprint + ghostline.
    const map = computeFeatureAccess(
      { canAccessFootprint: true } as Parameters<typeof computeFeatureAccess>[0],
      config,
    );
    expect(map.footprint).toBe(true);
    expect(map.ghostline).toBe(false); // switch on, plan flag missing
    expect(map.topup).toBe(false); // neither
  });

  it('returns a boolean for every feature id and nothing else', () => {
    const map = computeFeatureAccess(null);
    expect(Object.keys(map).sort()).toEqual([...FEATURE_IDS].sort());
    for (const id of FEATURE_IDS) expect(typeof map[id]).toBe('boolean');
  });
});

describe('parseFeatureConfig fails closed', () => {
  it('keeps enforcement on for a malformed payload', () => {
    for (const payload of [null, undefined, {}, 'nonsense', 42, []]) {
      expect(parseFeatureConfig(payload).enforced, JSON.stringify(payload) ?? 'undefined').toBe(
        true,
      );
    }
  });

  it('treats every non-true switch value as off', () => {
    const parsed = parseFeatureConfig({
      enforced: true,
      switches: { footprint: 'true', ghostline: 1, topup: true },
    });
    expect(parsed.switches.footprint).toBe(false); // string, not boolean
    expect(parsed.switches.ghostline).toBe(false); // number, not boolean
    expect(parsed.switches.topup).toBe(true);
  });

  it('only turns enforcement off for an explicit false', () => {
    expect(parseFeatureConfig({ enforced: false }).enforced).toBe(false);
    expect(parseFeatureConfig({ enforced: 'false' }).enforced).toBe(true);
    expect(parseFeatureConfig({ enforced: 0 }).enforced).toBe(true);
  });

  it('yields a switch entry for every feature id, ignoring unknown keys', () => {
    const parsed = parseFeatureConfig({ switches: { footprint: true, bogusFeature: true } });
    expect(Object.keys(parsed.switches).sort()).toEqual([...FEATURE_IDS].sort());
  });
});

describe('the retired NEXT_PUBLIC_ENABLE_* switches are gone from the client tree', () => {
  // The whole point of the move is that these names no longer exist. A single
  // reintroduced read puts the value back in the bundle, so scan the tree rather
  // than trusting the handful of files that used to hold them.
  it('appears in no source file under src/', () => {
    const files = walk(SRC_ROOT).filter((f) => /\.(ts|tsx)$/.test(f));
    const offenders = files.filter((file) => {
      // This test file necessarily names the pattern it forbids.
      if (file === __filename.replace(/\\/g, '/')) return false;
      return /NEXT_PUBLIC_ENABLE_/.test(stripComments(readFileSync(file, 'utf8')));
    });
    expect(offenders.map((f) => f.slice(SRC_ROOT.length + 1))).toEqual([]);
  });
});

/** Every file under `dir`, recursively, with forward slashes. */
function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name).replace(/\\/g, '/');
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === '.next') continue;
      out.push(...walk(full));
    } else {
      out.push(full);
    }
  }
  return out;
}
