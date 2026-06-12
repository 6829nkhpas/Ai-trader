# Implementation Plan: Professional Charting Suite

## Overview

This plan implements the Professional Charting Suite as a set of pure, deterministic TypeScript engines (chart-type transforms, indicator math, footprint/profile aggregation, strategy evaluation, drawing geometry, validation, persistence serialization) plus React/`lightweight-charts`/canvas rendering adapters that consume engine output. Engines are built first and validated with property-based tests (`fast-check` + Vitest, ≥100 runs each); rendering, pane synchronization, realtime behavior, crosshair, and persistence wiring are layered on top and validated with example/integration tests.

All file paths are relative to `frontend/src`. Existing components (`AlphaPredictiveChart`, `FootprintChart`, `VolumeProfileOverlay`, `ChartToolsBar`, `useTradeStore`, `useChartUIStore`) and the Tauri `save_workspace`/`load_workspace` IPC in `db.rs` are reused and generalized rather than replaced.

## Tasks

- [x] 1. Set up charting module foundations
  - [x] 1.1 Create charting module structure and shared value types
    - Create `charting/engines/`, `charting/types.ts`, and `charting/__tests__/` directories
    - Define `ChartCandle`, `LinePoint`, `LineStyleSpec`, `NumericRange`, `ValidationResult<T>` shared types
    - Ensure `fast-check` and Vitest are configured for the charting module
    - _Requirements: 1.1, 2.1, 3.1_

  - [x] 1.2 Implement validation utilities
    - Implement `validateNumeric(value, range, paramName)` and `validateParams(params, spec)` that reject non-numeric, wrong-type, and out-of-range values and return the offending parameter name
    - _Requirements: 1.6, 2.3, 2.5, 6.9, 7.2, 8.6_

  - [x]* 1.3 Write property test for parameter validation
    - **Property 3: Invalid parameters are rejected and last valid values are retained**
    - **Validates: Requirements 1.6, 2.5, 8.6**

  - [x] 1.4 Implement canonical candle series selector
    - Implement `canonicalCandles(raw, symbol)` producing a strictly ascending, timestamp-deduplicated series feeding all engines
    - Implement the in-place latest-candle update helper used by the renderer
    - _Requirements: 9.3, 9.6_

  - [x]* 1.5 Write property test for canonical candle series
    - **Property 28: Canonical candle series is sorted and de-duplicated**
    - **Validates: Requirements 9.6**

  - [x]* 1.6 Write property test for latest-candle live update
    - **Property 29: Live update of the latest candle changes only that candle**
    - **Validates: Requirements 9.3**

- [x] 2. Implement ChartTypeEngine
  - [x] 2.1 Implement chart-type transforms and parameter validation
    - Implement `buildSeries`, `computeHeikinAshi`, and `validateChartTypeParams` for all 11 chart types (candlestick, hollow candle, OHLC bar, line, area, baseline, Heikin Ashi, Renko, Kagi, Point & Figure, Line Break), emitting index-based series for brick/column types
    - _Requirements: 1.1, 1.5, 1.6, 1.7_

  - [x]* 2.2 Write property test for Heikin Ashi computation
    - **Property 1: Heikin Ashi close is the source candle average**
    - **Validates: Requirements 1.7**

  - [x]* 2.3 Write unit tests for chart-type registry and empty/error states
    - Assert all 11 chart types are supported; empty dataset yields empty-state output; fetch-failure path retains prior output
    - _Requirements: 1.1, 1.8, 1.9_

- [x] 3. Implement IndicatorEngine
  - [x] 3.1 Implement overlay indicators and registry
    - Implement SMA, EMA, WMA, Bollinger Bands, VWAP, Ichimoku, SuperTrend, Parabolic SAR, Donchian, Keltner with `minLookback`, `warmupBars`, and per-param `paramSpec`; register in `INDICATOR_REGISTRY`
    - _Requirements: 2.1, 2.2, 2.8, 2.9_

  - [x] 3.2 Implement oscillator indicators with reference levels
    - Implement RSI, MACD, Stochastic, ADX/DMI, ATR, OBV, CCI, MFI, Williams %R with reference levels (e.g. RSI 30/70); add `listIndicators`/`searchIndicators`
    - _Requirements: 3.1, 3.5_

  - [x]* 3.3 Write property test for EMA smoothing factor
    - **Property 2: EMA uses the standard smoothing factor**
    - **Validates: Requirements 2.9**

  - [x]* 3.4 Write property test for indicator plot completeness
    - **Property 5: Indicator plots contain every defined line, band, and reference level**
    - **Validates: Requirements 2.8, 3.5**

  - [x]* 3.5 Write property test for insufficient indicator data
    - **Property 4: Insufficient data omits computation and signals insufficiency**
    - **Validates: Requirements 2.6, 3.8**

  - [x]* 3.6 Write property test for indicator live append
    - **Property 6: Live append equals full recompute** (overlay + oscillator indicators)
    - **Validates: Requirements 2.7, 3.7**

  - [x]* 3.7 Write unit tests for indicator registry completeness
    - Assert the overlay list (2.1) and oscillator list (3.1) are all present and discoverable
    - _Requirements: 2.1, 3.1_

- [x] 4. Implement PaneManager
  - [x] 4.1 Implement pane creation, ordering, sync, and redistribution
    - Wrap `lightweight-charts` pane APIs; implement `ensurePane`, `removePaneIfEmpty`, `layout`, `syncVisibleRange`, and the pure `redistribute` helper
    - _Requirements: 3.2, 3.3, 3.4, 3.6_

  - [x]* 4.2 Write property test for oscillator pane ordering
    - **Property 7: Oscillator panes stack in addition order**
    - **Validates: Requirements 3.2**

  - [x]* 4.3 Write property test for pane removal redistribution
    - **Property 8: Pane removal redistributes height with no gap**
    - **Validates: Requirements 3.6**

- [x] 5. Implement Indicator Manager (store slice + panel)
  - [x] 5.1 Implement the indicator store slice with invariants
    - Extend `useChartUIStore` with `activeIndicators`, `addIndicator`, `removeIndicator`, `setIndicatorParams`, `setIndicatorStyle`, `toggleIndicatorVisible`; enforce duplicate rejection, 50-entry cap, and empty-list init for unknown symbols
    - _Requirements: 4.3, 4.4, 4.5, 4.7, 4.8, 4.11_

  - [x] 5.2 Build the IndicatorManagerPanel UI with search
    - Implement `IndicatorManagerPanel.tsx` listing all indicators with a case-insensitive search field
    - _Requirements: 4.1, 4.2, 4.6_

  - [x]* 5.3 Write property test for indicator search
    - **Property 9: Indicator search returns exactly the case-insensitive name matches**
    - **Validates: Requirements 4.2**

  - [x]* 5.4 Write property test for active-indicator add invariants
    - **Property 10: Active-indicator add invariants hold**
    - **Validates: Requirements 4.3, 4.4, 4.5**

  - [x]* 5.5 Write property test for visibility toggle preserving configuration
    - **Property 11: Toggling visibility preserves configuration** (active indicators)
    - **Validates: Requirements 4.7**

  - [x]* 5.6 Write unit tests for indicator management paths
    - Test duplicate-add and at-capacity indications, restyle redraw, and remove
    - _Requirements: 4.4, 4.5, 4.6, 4.8_

- [x] 6. Implement DrawingEngine
  - [x] 6.1 Implement drawing geometry, validation, and model extension
    - Implement `TOOL_REGISTRY`, `fibLevels`, `magnetSnap`, `isComplete`, `clearUnlocked`, and the `{time,price}`↔pixel coordinate transforms; extend `Drawing` with `locked` and `symbol`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6, 5.10_

  - [x] 6.2 Wire the drawing store slice and renderer for lock/visibility/clear
    - Connect `useChartUIStore` drawing actions and `useDrawingRenderer`/`DrawingOverlays` for create, drag-edit, lock immutability, visibility toggle, clear-unlocked, hover, and eraser delete
    - _Requirements: 5.5, 5.7, 5.8, 5.9, 10.5, 10.7_

  - [x]* 6.3 Write property test for drawing anchor-count creation
    - **Property 12: Drawing creation requires exactly the tool's anchor count**
    - **Validates: Requirements 5.2, 5.3**

  - [x]* 6.4 Write property test for anchor coordinate round-trip
    - **Property 13: Drawing anchors survive a coordinate round-trip**
    - **Validates: Requirements 5.4**

  - [x]* 6.5 Write property test for magnet snapping
    - **Property 14: Magnet snaps to the nearest OHLC within threshold, else to the pointer**
    - **Validates: Requirements 5.6**

  - [x]* 6.6 Write property test for locked-drawing immutability
    - **Property 15: Locked drawings are immutable**
    - **Validates: Requirements 5.7**

  - [x]* 6.7 Write property test for clearing unlocked drawings
    - **Property 16: Clear removes unlocked drawings and retains locked ones**
    - **Validates: Requirements 5.9**

  - [x]* 6.8 Write property test for Fibonacci retracement levels
    - **Property 17: Fibonacci retracement levels match the canonical ratios**
    - **Validates: Requirements 5.10**

  - [x]* 6.9 Write unit tests for drawing interaction paths
    - Test drag updates geometry, eraser deletes clicked drawing, hover state differs
    - _Requirements: 5.5, 10.5, 10.7_

- [ ] 7. Checkpoint - core geometry and indicator engines
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement FootprintEngine
  - [x] 8.1 Extract and implement footprint aggregation
    - Implement `buildFootprint`, `cumulativeDelta`, `detectImbalances` with tick-size clustering, delta/total volume, POC with close tie-break, imbalance ratio (1.5–20, default 3), and synthetic fallback
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9_

  - [x] 8.2 Wire the FootprintChart canvas renderer to the engine
    - Render bid/ask clusters, imbalance highlight, POC mark, per-candle footer, synthetic indication, and waiting-for-data state; regroup on tick-size change
    - _Requirements: 6.1, 6.3, 6.8, 6.9, 6.11_

  - [x]* 8.3 Write property test for delta and cumulative delta
    - **Property 18: Footprint delta and cumulative delta are correct sums**
    - **Validates: Requirements 6.4, 6.5, 6.8**

  - [x]* 8.4 Write property test for tick-size clustering
    - **Property 19: Footprint clustering groups by tick size and conserves volume**
    - **Validates: Requirements 6.1, 6.2, 6.9**

  - [x]* 8.5 Write property test for synthetic fallback
    - **Property 20: Footprint falls back to a flagged synthetic distribution**
    - **Validates: Requirements 6.3**

  - [x]* 8.6 Write property test for imbalance detection
    - **Property 21: Imbalance flags exactly the levels meeting the configured ratio**
    - **Validates: Requirements 6.6**

  - [x]* 8.7 Write property test for POC selection and tie-break
    - **Property 22: Footprint POC is the greatest-volume level with close tie-break**
    - **Validates: Requirements 6.7**

  - [x]* 8.8 Write property test for footprint live append
    - **Property 6: Live append equals full recompute** (footprint)
    - **Validates: Requirements 6.10**

- [x] 9. Implement VolumeProfileEngine
  - [x] 9.1 Extract and implement volume-profile binning
    - Implement `buildProfile` and `valueArea` for visible/session/fixed ranges with row binning (1–1000, default 24), POC, value area (1–100%, default 70), and null markers on zero volume
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.6, 7.9, 7.10_

  - [x] 9.2 Wire the VolumeProfileOverlay canvas to the engine
    - Render per-row bars with distinct value-area styling, POC/VAH/VAL markers, developing value area, empty-profile indication, debounced visible-range recompute, and fixed-range anchors
    - _Requirements: 7.1, 7.5, 7.6, 7.7, 7.8, 7.9_

  - [x]* 9.3 Write property test for profile binning
    - **Property 23: Volume profile binning conserves volume and row count**
    - **Validates: Requirements 7.2, 7.6**

  - [x]* 9.4 Write property test for POC and value area
    - **Property 24: Volume profile POC and value area are correct**
    - **Validates: Requirements 7.3, 7.4, 7.7, 7.8, 7.9**

  - [x]* 9.5 Write property test for invalid fixed range
    - **Property 25: Invalid fixed range is rejected and the prior profile is retained**
    - **Validates: Requirements 7.10**

- [x] 10. Implement StrategyEngine
  - [x] 10.1 Implement strategy registry and evaluation
    - Implement `STRATEGY_REGISTRY` with at least MA-crossover, RSI mean-reversion, and breakout strategies; `evaluate`, `requiredLookback`, `summarize`; return `[]` on insufficient lookback
    - _Requirements: 8.1, 8.2, 8.3, 8.5, 8.6, 8.9_

  - [x]* 10.2 Write property test for well-formed signals
    - **Property 26: Strategy signals are well-formed and anchored to candles**
    - **Validates: Requirements 8.2**

  - [x]* 10.3 Write property test for strategy summary
    - **Property 27: Strategy summary reports a consistent count and numeric net result**
    - **Validates: Requirements 8.9**

  - [x]* 10.4 Write property test for strategy live append
    - **Property 6: Live append equals full recompute** (strategy)
    - **Validates: Requirements 8.7**

  - [x]* 10.5 Write unit tests for strategy registry and removal
    - Assert ≥3 strategies incl. the three named, insufficient-data indication, and marker removal on strategy remove
    - _Requirements: 8.1, 8.3, 8.8_

- [ ] 11. Checkpoint - order-flow and strategy engines
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Implement ChartRenderer and ChartSurface
  - [x] 12.1 Generalize the renderer to consume engine output
    - Generalize `AlphaPredictiveChart` into `ChartRenderer` that renders `ChartTypeEngine` series, overlay indicator series, and oscillator panes via `PaneManager`
    - _Requirements: 2.2, 2.4, 2.8, 3.5, 8.4_

  - [x] 12.2 Implement realtime update behaviors
    - In-place last-candle update, clear-on-symbol-switch, right-edge follow, out-of-order repaint from canonical series, and the disconnected-feed indicator
    - _Requirements: 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_

  - [ ] 12.3 Implement zoom clamping, DPR rendering, and volume proxy label
    - Clamp wheel zoom to 5–5000 visible candles, render DPR-aware for ratios 1.0–4.0, and show the price-range-proxy label for zero-volume indices
    - _Requirements: 10.6, 12.6, 12.7_

  - [x] 12.4 Build the ChartSurface shell with controls and dialogs
    - Mount persistent controls (chart-type selector, indicator-manager entry, drawing toolbar, chart-mode toggle, timeframe selector, strategy entry) and overlay settings dialogs; implement fullscreen with failure fallback
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [ ]* 12.5 Write property test for wheel zoom bounds
    - **Property 30: Wheel zoom keeps the visible candle count within bounds**
    - **Validates: Requirements 10.6**

  - [ ]* 12.6 Write unit/integration tests for renderer behaviors
    - Test symbol-switch clear, right-edge follow, disconnect/reconnect indicator, fullscreen failure, and DPR backing-store scaling
    - _Requirements: 9.4, 9.5, 9.7, 9.8, 12.5, 12.6_

- [ ] 13. Implement CrosshairController
  - [x] 13.1 Implement synchronized crosshair and readouts
    - Subscribe to crosshair moves; read OHLC + active-indicator values, format to instrument precision, broadcast a synced vertical crosshair across panes, and show no-value placeholders for warm-up/out-of-range positions
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.8_

  - [ ]* 13.2 Write property test for value formatting precision
    - **Property 31: Values are formatted to the instrument's configured precision**
    - **Validates: Requirements 10.1, 10.2**

  - [ ]* 13.3 Write property test for no-value placeholder
    - **Property 32: Out-of-range or warm-up positions yield a no-value placeholder**
    - **Validates: Requirements 10.3, 10.8**

- [x] 14. Implement WorkspaceStore persistence
  - [x] 14.1 Implement workspace serialization and persistence
    - Implement `WorkspaceState`, `serializeWorkspace`, `deserializeWorkspace`, `DEFAULT_WORKSPACE`; wire debounced `save_workspace`/`load_workspace` IPC with in-memory fallback outside Tauri
    - _Requirements: 1.3, 1.4, 4.9, 4.10, 5.11, 11.1, 11.2, 11.3, 11.6_

  - [ ]* 14.2 Write property test for serialization round-trip
    - **Property 33: Workspace serialization round-trips**
    - **Validates: Requirements 1.3, 4.9, 4.10, 5.11, 11.1, 11.2**

  - [ ]* 14.3 Write property test for absent persisted workspace
    - **Property 34: Absent persisted workspace yields defaults**
    - **Validates: Requirements 1.4, 4.11, 11.3**

  - [ ]* 14.4 Write unit tests for persist/restore failure handling
    - Test restore-failure applies defaults and retains on-screen state; persist-failure retains in-memory state and retries on next change
    - _Requirements: 5.12, 11.4, 11.5_

- [ ] 15. Integration and wiring
  - [ ] 15.1 Wire the full charting suite into MainTerminalChart
    - Connect engines, stores, renderer, pane manager, crosshair, and persistence through `ChartSurface` mounted in `MainTerminalChart`, fed by the canonical candle selector
    - _Requirements: 9.1, 11.1, 12.1_

  - [ ]* 15.2 Write integration and performance tests
    - Test pane time-bound sync and pan/zoom follow, synchronized crosshair across panes, persistence round-trip through the real SQLite IPC, and latency/frame-budget targets
    - _Requirements: 3.3, 3.4, 9.1, 9.2, 10.4, 11.1, 11.2_

- [ ] 16. Final checkpoint - full suite verification
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirement clauses for traceability, and every correctness property from the design maps to exactly one property-test sub-task.
- Property tests use `fast-check` + Vitest at ≥100 runs each and are tagged `// Feature: professional-charting-suite, Property {n}`.
- Property 6 (live append equals full recompute) is validated per-engine across tasks 3.6, 8.8, and 10.4 against its respective requirement clauses.
- Property 11 covers both indicators (task 5.5) and drawings; drawing visibility/lock behavior is exercised in tasks 6.6/6.7.
- Checkpoints (tasks 7, 11, 16) provide incremental validation breaks and are not part of the dependency graph.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.4"] },
    { "id": 2, "tasks": ["1.3", "1.5", "1.6", "2.1", "3.1", "4.1", "6.1", "8.1", "9.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "3.2", "4.2", "4.3", "5.1", "6.2", "8.2", "9.2", "10.1"] },
    { "id": 4, "tasks": ["3.3", "3.4", "3.5", "3.6", "3.7", "5.2", "5.3", "5.4", "5.5", "5.6", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "6.9", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "9.3", "9.4", "9.5", "10.2", "10.3", "10.4", "10.5", "12.1", "14.1"] },
    { "id": 5, "tasks": ["12.2", "12.4", "13.1", "14.2", "14.3", "14.4"] },
    { "id": 6, "tasks": ["12.3", "13.2", "13.3"] },
    { "id": 7, "tasks": ["12.5", "12.6", "15.1"] },
    { "id": 8, "tasks": ["15.2"] }
  ]
}
```
