// Coverage contract between the app's backend calls and the adapter registry.
//
// The failure this guards against is specific: a component calls a command that
// has no adapter, and the user sees a runtime error where a panel should be. That
// was originally an undefined-`invoke` TypeError from the Tauri shell; it is now a
// `BridgeUnsupportedError`, which is honest but still a broken panel.
//
// The old version of this test derived its command list from
// `src-tauri/src/lib.rs`'s `invoke_handler![]`. That file is gone with the desktop
// shell, and the invariant it encoded ("every registered Rust command is
// classified") no longer describes anything real. The stronger invariant — the one
// that actually protects the user — is the reverse: every command the frontend
// ACTUALLY CALLS must resolve. So the source of truth is now the call sites
// themselves, scanned off disk for the same reason: a hand-maintained list drifts.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
  NATIVE_BROWSER_PATH,
  NO_FRONTEND_CALLER,
  NOT_APPLICABLE_ON_WEB,
  PENDING_SERVER_ROUTE,
  WEB_ADAPTERS,
} from '../webAdapters';

const SRC = fileURLToPath(new URL('../../..', import.meta.url));

/** Every `.ts`/`.tsx` file under `src/`, excluding tests. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '__tests__') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      sourceFiles(full, out);
    } else if (/\.tsx?$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Command names passed to `bridgeInvoke` as string literals across `src/`.
 *
 * Literal-only by necessity: a dynamically-built command name cannot be resolved
 * statically. There is currently no such call site — the one former indirection
 * (`useQuantStore`'s `tauriInvoke` alias) was collapsed into `bridgeInvoke` — and
 * the test below fails if one reappears, so this cannot silently under-report.
 */
function invokedCommands(): { commands: string[]; dynamicCallSites: string[] } {
  const commands = new Set<string>();
  const dynamicCallSites: string[] = [];

  for (const file of sourceFiles(SRC)) {
    // Skip the bridge's own modules: `webAdapters` names every command in both
    // tables (scanning it would make the test vacuous) and `index` DECLARES
    // `bridgeInvoke(command, args)`, whose parameter would read as a dynamic call.
    if (file.includes(join('lib', 'bridge'))) continue;
    const src = readFileSync(file, 'utf8');

    for (const m of src.matchAll(/bridgeInvoke(?:<[^>]*>)?\(\s*(['"`])([a-z_][a-z0-9_]*)\1/g)) {
      commands.add(m[2]);
    }
    // A call whose first argument is not a string literal.
    for (const m of src.matchAll(/bridgeInvoke(?:<[^>]*>)?\(\s*([^'"`\s)])/g)) {
      dynamicCallSites.push(`${file.slice(SRC.length)}: bridgeInvoke(${m[1]}…`);
    }
  }

  return { commands: [...commands].sort(), dynamicCallSites };
}

const { commands: INVOKED, dynamicCallSites: DYNAMIC } = invokedCommands();

/** Commands deliberately without an adapter, each with a stated reason. */
const UNSUPPORTED = {
  NATIVE_BROWSER_PATH,
  NOT_APPLICABLE_ON_WEB,
  PENDING_SERVER_ROUTE,
  NO_FRONTEND_CALLER,
} as const;

describe('bridge command coverage', () => {
  it('finds the real call sites', () => {
    // Sanity: if the scan silently returned nothing, the test below would
    // vacuously pass.
    expect(INVOKED.length).toBeGreaterThan(15);
    expect(INVOKED).toContain('fetch_symbol_sentiment');
    expect(INVOKED).toContain('search_instruments');
  });

  it('has an adapter for every command the app actually calls', () => {
    const missing = INVOKED.filter((c) => !(c in WEB_ADAPTERS));
    expect(
      missing,
      'each of these is invoked by a component but has no web adapter — either ' +
        'add one to WEB_ADAPTERS or stop calling it',
    ).toEqual([]);
  });

  it('resolves every command name statically', () => {
    // A dynamic name would slip past the check above, so the escape hatch is
    // closed rather than trusted.
    expect(DYNAMIC).toEqual([]);
  });

  it('does not list a called command as unsupported', () => {
    // The contradiction this catches: a command sitting in PENDING_SERVER_ROUTE
    // while a component calls it — which is exactly the reported crash.
    const contradictions: string[] = [];
    for (const [table, entries] of Object.entries(UNSUPPORTED)) {
      for (const cmd of Object.keys(entries)) {
        if (INVOKED.includes(cmd)) contradictions.push(`${table}.${cmd}`);
      }
    }
    expect(contradictions).toEqual([]);
  });

  it('classifies each command in exactly one table', () => {
    const tables = { WEB_ADAPTERS, ...UNSUPPORTED };
    const seen = new Map<string, string[]>();
    for (const [name, table] of Object.entries(tables)) {
      for (const cmd of Object.keys(table)) {
        seen.set(cmd, [...(seen.get(cmd) ?? []), name]);
      }
    }
    const duplicated = [...seen.entries()].filter(([, names]) => names.length > 1);
    expect(duplicated).toEqual([]);
  });

  it('gives every deliberately-unsupported command a human reason', () => {
    const empty: string[] = [];
    for (const [tableName, table] of Object.entries(UNSUPPORTED)) {
      for (const [cmd, reason] of Object.entries(table)) {
        if (typeof reason !== 'string' || reason.trim().length < 10) {
          empty.push(`${tableName}.${cmd}`);
        }
      }
    }
    expect(empty).toEqual([]);
  });

  it('exposes every adapter as a callable', () => {
    for (const [cmd, fn] of Object.entries(WEB_ADAPTERS)) {
      expect(typeof fn, `${cmd} adapter must be a function`).toBe('function');
    }
  });
});
