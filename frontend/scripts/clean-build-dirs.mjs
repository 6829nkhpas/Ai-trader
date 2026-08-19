// Clears the Next.js build directories around the Tauri static-export build.
//
// WHY THIS EXISTS
// ---------------
// `next dev` and `next build` write different, mutually incompatible artifact
// sets into the same `.next` directory, and neither clears the other's output.
// When a `.next` left behind by one mode is picked up by the other, the dev
// server can fail to resolve a per-route client reference manifest and answers
// every request with an HTTP 500 whose body is an empty shell:
//
//   InvariantError: Expected clientReferenceManifest to be defined
//
// In a browser that renders the Next error overlay. Inside the Tauri webview it
// is a completely blank window with no text at all, which is close to
// undiagnosable from the UI — the app looks dead rather than misconfigured. That
// is the failure this script prevents.
//
// The asymmetry that decides the design: `npm run dev` is run constantly and
// benefits from its incremental cache, while `build:desktop` is slow and rare.
// So dev is never cleaned (it keeps its cache and stays fast) and the export
// build cleans on BOTH sides — `--pre` so the export cannot inherit dev
// artifacts, `--post` so the production intermediates it leaves behind cannot
// poison the next `next dev`.
//
// `--post` deliberately removes `.next` but NOT `out`: `out` is the exported
// site that tauri.conf.json bundles via `frontendDist: "../out"`, so deleting it
// would leave the desktop build with no frontend — the same blank window by a
// different route.
//
// Only the static-export path (`build:desktop`) cleans. The hosted web build
// (`npm run build`) must keep `.next`, because `npm run start` serves from it.
//
// No dependency is used (the repo has no rimraf/shx) — fs.rmSync covers it.

import { rmSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// Default (no flag) is the full manual reset: `npm run clean`, for when dev is
// already wedged and the cache needs to go.
const mode = process.argv[2] ?? '--all';

const TARGETS = {
  '--pre': ['.next', 'out'],
  '--post': ['.next'],
  '--all': ['.next', 'out'],
};

const targets = TARGETS[mode];
if (!targets) {
  console.error(`clean-build-dirs: unknown mode "${mode}" (expected --pre, --post or no argument)`);
  process.exit(1);
}

for (const target of targets) {
  const absolute = path.join(frontendRoot, target);
  if (!existsSync(absolute)) continue;
  rmSync(absolute, { recursive: true, force: true });
  console.log(`clean-build-dirs: removed ${target}`);
}
