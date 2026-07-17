# Ghost Line Stability Fix — Work Plan

## Context (from research)

The ghost line is a forward price projection drawn on the TradingView chart. Four engines
(`ghostLineMode` in `useChartUIStore`): `linear` (OLS), `volume` (VWLR), `curved` (VWEPR),
`forecast` (volatility-aware EWMA). Compute lives in `frontend/src/hooks/ghostLineComputation.ts`;
drawing + redraw triggering lives in `frontend/src/hooks/useGhostLine.ts`; the Rust engines are
`frontend/src-tauri/src/quant/{predictive.rs,vwepr.rs}` exposed via the `compute_ghost_curve`
command in `frontend/src-tauri/src/commands/quant.rs`. Mode selector: `GhostLineToggle.tsx`.

A deep code review found the concrete defects that make the line "unstable" (flickers, misaligned
on aggregated timeframes, mode-switching does nothing, redraw storms). The fixes are small and
localized. They are sliced into 7 independent worktree units so each lands as its own PR.

### Confirmed bugs (file:line verified)

1. **Interval uses base interval on aggregated timeframes** — `ghostLineComputation.ts:511-513`
   prefers `inferBarIntervalSec` (median gap of stored bars, which are at the *Kite base*
   interval) over `TIMEFRAME_MS` (the *display* interval). On `2m/4m/75m/125m/2h/3h/4h/1W/1M`
   the projection steps at the base interval → 2–5× too many points, off-grid → jitter.
2. **Path 1 predictive signal ignores `ghostLineMode` and overrides all four engines** —
   `ghostLineComputation.ts:526-545`. Whenever a predictive signal streams in, OLS/VWLR/VWEPR/
   Forecast all render the *same* straight interpolation → mode toggle does nothing.
3. **Clear-then-draw leaves a visible gap** — `useGhostLine.ts:264` removes old segments before
   the async `drawGhostSegments` (one `createMultipointShape` per segment, awaited) creates new
   ones → flicker / "appears then disappears."
4. **`predictiveKey` dep bypasses the 4s redraw throttle** — `useGhostLine.ts:140-149,298`.
   Every `predictive-tick` changes `predicted_close_price` and re-fires the effect immediately;
   the realtime `pulse` throttle (line 204) becomes dead → redraw storm.
5. **`onVisibleRangeChanged` fires on every new-bar auto-scroll, not just user zoom** —
   `useGhostLine.ts:170-175`. TV auto-scrolls the right edge on each new bar → `setZoomPulse` →
   effect reruns. A new bar already bumps `lastBarTime`, so each new bar = 2–3 concurrent draws.
6. **Clamp can flatten VWEPR/Forecast curves** — `ghostLineComputation.ts:605-625`.
   `maxTotal = avgStep * 40 * (N-1)` bites for accelerating curves on volatile instruments → flat
   segment ("wrong direction").  Straight modes correctly skip the clamp.
7. **Failed `removeEntity` ids accumulate in `entityIdsRef` forever** — `useGhostLine.ts:17-30,264`.
   On widget recreate the old ids are invalid → `removeEntity` always fails → ids grow unbounded,
   warn spam, transient double-line on rapid switches.

### Conventions discovered (workers must follow)

- **Tauri detection:** `const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;` — already in both files.
- **Console tag:** every log uses the `[GhostLine]` prefix — keep it.
- **Pure-JS engines (`olsProjection`, `vwlrProjection`, `vweprProjection`, `forecastProjection`,
  `nextSessionSlots`, `inferBarIntervalSec`) are exported/testable as plain functions** — keep them
  pure (no React, no store). The existing charting test suite uses vitest + fast-check property
  tests under `src/charting/__tests__`; mirror that style for new ghost tests (place at
  `src/hooks/__tests__/ghostLineComputation.spec.ts`).
- **No new deps.** Use only what's in `package.json` (vitest, fast-check, testing-library).
- **Match existing comment density** — these files are heavily commented; preserve and update the
  rationale comments when changing behavior.
- **Typeframe types:** `Timeframe` and `TIMEFRAME_MS` from `src/utils/chartTypes.ts`.

## Work units

### Unit 1 — Fix projection interval to use the display timeframe
- **Files:** `frontend/src/hooks/ghostLineComputation.ts`
- **Change:** At line 511-513, swap precedence to prefer the display timeframe:
  `const intervalSec = mapInterval > 0 ? mapInterval : barInterval;`. Keep `barInterval` as a
  sanity fallback only (and log when it's used). Update the comment block (lines 507-513) to
  explain the aggregated-timeframe case (stored bars are at the Kite *base* interval, so the
  inferred gap is wrong for `2m/4m/75m/125m/2h/3h/4h/1W/1M`). Add a vitest case asserting
  `intervalSec === 120` for a `2m` lookback of 1-minute-spaced bars, and `=== 4500` for `75m`.

### Unit 2 — Gate Path 1 (predictive signal) by engine mode
- **Files:** `frontend/src/hooks/ghostLineComputation.ts`
- **Change:** Path 1 (lines 525-545) currently overrides every mode whenever a predictive signal
  exists. Gate it so the predictive signal only drives the projection when
  `ghostLineMode === 'forecast'` (the ML/forward-looking engine). For `linear`/`volume`/`curved`
  the user-selected regression engine must win (Path 2 / Path 3). Keep the `dev < 0.20` and
  `targetSec` validity guards. Update the section comment (line 525) to reflect that Path 1 is
  now the forecast-mode ML path. Add a vitest case: with a valid predictive signal AND
  `ghostLineMode === 'linear'`, the returned points must come from the OLS projection
  (deterministic slope), NOT the signal interpolation.

### Unit 3 — Double-buffer the draw (eliminate clear-then-draw flicker)
- **Files:** `frontend/src/hooks/useGhostLine.ts`
- **Change:** Lines 260-283. Instead of clearing `entityIdsRef.current` *before* drawing, draw the
  new segments first into a local `newIds` array (via `drawGhostSegments`), then remove the
  *previous* ids (`prevIds = entityIdsRef.current`) and set `entityIdsRef.current = newIds`.
  Handle the stale-mid-draw case: if `isStale()` after the draw, remove `newIds` and keep
  `prevIds` (the prior run owns the chart). Keep the `shouldAbort` polling inside
  `drawGhostSegments`. Update the comments at 260-283. Add a vitest (testing-library +
  `renderHook` if feasible, or a pure-logic test of the id-swap ordering) — if `renderHook` with a
  mocked `widget` is too heavy, instead extract the id-lifecycle into a tiny pure helper and
  unit-test that; note the approach in the PR.

### Unit 4 — Drop `predictiveKey` from the redraw dep array
- **Files:** `frontend/src/hooks/useGhostLine.ts`
- **Change:** The effect already reads `useTradeStore.getState().predictiveSignals` at run-time
  (line 244) — the `predictiveKey` selector (lines 140-149) is therefore redundant AND it
  re-fires the effect on every predictive tick, bypassing the 4s `pulse` throttle. Remove the
  `predictiveKey` selector and drop it from the dep array (line 298) so predictive signals are
  only re-consumed on the throttled cadence (`lastBarTime`, `pulse`, `zoomPulse`, mode/symbol/tf
  changes). Update the comment at 242-244 to state this explicitly. Add a vitest asserting the
  effect does not re-run when only `predictiveSignals` changes (use a mocked widget + renderHook,
  counting draws).

### Unit 5 — Debounce `zoomPulse` and ignore programmatic auto-scroll
- **Files:** `frontend/src/hooks/useGhostLine.ts`
- **Change:** Lines 163-183. (a) Raise the zoom throttle from 400ms to ~800-1000ms. (b) Track
  the last `vr.from` / `vr.to`; only `setZoomPulse` when `from` changes (user pan) or the range
  *width* `to - from` changes (user zoom) — NOT when only `to` edges forward because a new bar
  auto-scrolled the chart. (c) Guard the `onChartReady` subscription against cleanup-before-ready:
  add a `disposed` flag checked inside the `onChartReady` callback before subscribing, and store
  the unsubscribe handler so cleanup can unsubscribe even if `onChartReady` fired late. Update
  comments. Add a vitest for the "only-`to` changed → no pulse" decision logic (extract it to a
  small pure helper `shouldPulseOnRangeChange(prev, next)` and test it).

### Unit 6 — Loosen the curve clamp so VWEPR/Forecast aren't flattened
- **Files:** `frontend/src/hooks/ghostLineComputation.ts`
- **Change:** Lines 599-625. The clamp is correct to skip for `linear`/`volume`
  (`isStraightLine`). For `curved`/`forecast`, raise the limits so genuine accelerating curves
  survive: e.g. `maxStep = avgStep * 12` and `maxTotal = maxStep * (points.length - 1) * 2`
  (scale total with projection length, not a flat 5×). Keep the per-step cap as a guard against
  truly pathological spikes only. Update the comment block (599-604) to explain the new tuning.
  Add a vitest: a VWEPR projection over quadratic input must be monotonic-accelerating and NOT
  clipped to a flat segment under the new limits (assert the last projected price differs from
  the anchor by more than `avgStep` on accelerating data).

### Unit 7 — Bound failed-remove id accumulation
- **Files:** `frontend/src/hooks/useGhostLine.ts`
- **Change:** Lines 17-30 and the cleanup at 289-297. (a) On widget teardown/cleanup, clear
  `entityIdsRef.current = []` after the best-effort remove (don't retain ids for a dead widget).
  (b) For live redraws, only retry a failed-remove id a bounded number of times (e.g. drop after
  2 consecutive failures) — track a small `failedAttempts: Map<string, number>` or a parallel
  ref. (c) In the main draw, when a new generation starts, do not carry the previous generation's
  failed ids forward unconditionally. Update comments. Add a vitest for the bounded-retry helper
  (extract `pruneFailedIds(failed, attempts)` as a pure function).

## E2E test recipe (per worker)

Workers cannot drive live market data. Verification is **unit tests + type build + a Playwright
smoke stub**:

1. **Unit tests (required):** `cd frontend && npx vitest run` — must pass, including the new
   spec the worker adds under `src/hooks/__tests__/`. If `vitest` is unavailable, `npm test`.
2. **Type build (required):** `cd frontend && npx tsc --noEmit` (or `npm run build` if faster to
   run) — must pass with no new type errors.
3. **Playwright smoke (best-effort, do not block the PR on it):** A new spec
   `frontend/tests/ghost-line.spec.ts` that, under `ALPHA_TEST_MODE=1`, loads the app, opens the
   chart, clicks the GhostLine toggle (`#tv-btn-ghost-line` or the `GhostLineToggle` button) and
   cycles the 4 modes, asserting no uncaught `pageerror` and that the toggle's active class
   updates. Run with `npx cross-env ALPHA_TEST_MODE=1 npx playwright test ghost-line.spec.ts`
   (the existing `playwright.config.ts` already starts `next dev --port 1420`). If the
   TradingView iframe or Tauri mocking makes the smoke test flaky, mark it `test.fixme` with a
   note rather than failing the PR — the unit tests + build are the real gate.
4. **Rust (only relevant for Units that touch `.rs` — none of these do, but if a worker touches
   `quant.rs`/`predictive.rs`/`vwepr.rs`):** `cd frontend/src-tauri && cargo test` + `cargo build`.

If a worker's change is in `ghostLineComputation.ts` or `useGhostLine.ts` only, no Rust build is
needed.

## Worker instructions (shared — copy verbatim into each agent prompt)

```
After you finish implementing the change:
1. **Code review** — Invoke the `Skill` tool with `skill: "code-review"` to find correctness bugs (it reports findings; it does not edit code). Fix any findings it surfaces before continuing.
2. **Run unit tests** — Run the project's test suite (check for package.json scripts, Makefile targets, or common commands like `npm test`, `bun test`, `pytest`, `go test`). If tests fail, fix them.
3. **Test end-to-end** — Follow the e2e test recipe from the coordinator's prompt (below). If the recipe says to skip e2e for this unit, skip it.
4. **Commit and push** — Commit all changes with a clear message, push the branch, and create a PR with `gh pr create`. Use a descriptive title. If `gh` is not available or the push fails, note it in your final message.
5. **Report** — End with a single line: `PR: <url>` so the coordinator can track it. If no PR was created, end with `PR: none — <reason>`.
```

Use `subagent_type: "general-purpose"` unless a more specific agent type fits.
