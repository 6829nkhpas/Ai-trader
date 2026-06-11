# Requirements Document

## Introduction

This feature elevates the trading-chart experience of the Ai-trader terminal to a professional, premium-platform standard comparable to TradingView, Bookmap, and Sierra Chart. Traders spend the majority of their time on the chart, so the charting surface must be fast, visually precise, and feature-complete.

The work covers seven trader-facing pillars built on top of the existing `lightweight-charts` + Tauri (Rust) stack:

1. **Professional chart types** — candlestick, hollow candle, OHLC bars, line, area, baseline, Heikin Ashi, Renko, Kagi, Point & Figure, and Line Break.
2. **A full technical indicator library** — price-overlay indicators (MA/EMA/WMA, Bollinger Bands, VWAP, Ichimoku Cloud, SuperTrend, Parabolic SAR, Donchian/Keltner channels) and separate-pane oscillators (RSI, MACD, Stochastic, ADX/DMI, ATR, OBV, CCI, MFI, Williams %R), each with configurable parameters.
3. **Functional drawing tools** — the existing toolbar (trend lines, channels, Fibonacci, patterns, shapes, text, projection/measure) wired to a working render-and-edit engine with magnet, lock, and visibility controls.
4. **A professional Footprint (bid/ask cluster) chart** — imbalance highlighting, delta and cumulative delta, per-candle and session POC, and value area.
5. **A professional Volume Profile** — visible-range, session, and fixed-range modes with POC, value-area high/low (VAH/VAL), and developing value area.
6. **Trading strategies / strategy overlays** — selectable strategies that compute entry/exit signals and render markers on the chart.
7. **Flawless real-time animation and interaction** — smooth pan/zoom/crosshair, synchronized multi-pane layout, and low-latency live updates from the order-flow and OHLC feeds.

The scope also includes supporting UX/design improvements (chart-type selector, indicator manager panel, settings dialogs) and workspace persistence so a trader's layout survives across sessions and symbol switches.

This document defines WHAT the charting suite must do. Implementation choices (specific algorithms, component structure) are deferred to the design phase.

## Glossary

- **Charting_Suite**: The complete charting subsystem rendered inside `MainTerminalChart`, including all chart types, indicators, drawing tools, footprint, volume profile, and strategy overlays.
- **Chart_Renderer**: The component that draws price series using `lightweight-charts` for the Standard chart surface.
- **Chart_Type_Engine**: The subsystem that transforms raw OHLCV candles into the data series for a selected chart type (e.g., Heikin Ashi, Renko, Point & Figure) and renders it.
- **Indicator_Engine**: The subsystem that computes technical-indicator values from candle data.
- **Indicator_Manager**: The UI and state subsystem that lets a trader add, configure, reorder, hide, and remove indicators.
- **Overlay_Indicator**: An indicator drawn on the price scale (e.g., EMA, Bollinger Bands, VWAP, Ichimoku, SuperTrend).
- **Oscillator_Indicator**: An indicator drawn in a separate sub-pane with its own scale (e.g., RSI, MACD, Stochastic).
- **Indicator_Pane**: A horizontally-split sub-chart below the price pane that hosts one or more oscillator indicators and shares the price pane's time scale.
- **Pane_Manager**: The subsystem that manages creation, sizing, ordering, and time-axis synchronization of the price pane and indicator panes.
- **Drawing_Engine**: The subsystem that creates, renders, hit-tests, edits, and persists user drawings (lines, channels, Fibonacci, shapes, text, projections).
- **Footprint_Engine**: The subsystem that aggregates order-flow ticks into per-candle bid/ask price-level clusters and renders the Footprint chart.
- **Volume_Profile_Engine**: The subsystem that bins traded volume by price level and renders the volume profile with POC and value area.
- **Strategy_Engine**: The subsystem that evaluates a selected trading strategy over candle data and produces entry/exit signals.
- **Crosshair_Controller**: The subsystem that handles crosshair movement, OHLC/indicator readouts, and synchronized crosshair across panes.
- **Realtime_Feed**: The combined live data sources — the OHLC WebSocket / Tauri live-candle stream and the order-flow (L2) WebSocket — exposed via `useTradeStore`.
- **Workspace_Store**: The persistence subsystem (`useChartUIStore` + Tauri SQLite IPC) that saves and restores chart layout, chart type, indicators, drawings, and per-symbol settings.
- **POC**: Point of Control — the price level with the highest traded volume in a profile.
- **Value_Area**: The contiguous price range around the POC containing a configured percentage (default 70%) of total volume.
- **VAH / VAL**: Value Area High / Value Area Low — the upper and lower bounds of the Value_Area.
- **Delta**: Ask-initiated volume minus bid-initiated volume for a price level or candle.
- **Cumulative_Delta**: The running sum of per-candle Delta across the visible session.
- **Imbalance**: A price level where bid or ask volume exceeds the diagonally-opposite level by a configured ratio (default 3:1).
- **Tick_Size**: The price increment used to group order-flow into Footprint rows.
- **Timeframe**: A candle aggregation interval (e.g., 1m, 5m, 1D) as defined in `chartTypes.ts`.
- **Frame_Budget**: The 16-millisecond per-frame rendering target corresponding to 60 frames per second.

## Requirements

### Requirement 1: Chart Type Selection

**User Story:** As a trader, I want to switch between professional chart types, so that I can analyze price action in the representation that best suits my strategy.

#### Acceptance Criteria

1. THE Chart_Type_Engine SHALL support the following 11 price chart types: candlestick, hollow candlestick, OHLC bar, line, area, baseline, Heikin Ashi, Renko, Kagi, Point & Figure, and Line Break.
2. WHEN a trader selects a chart type from the chart-type selector, THE Chart_Type_Engine SHALL render the current symbol and Timeframe using the selected chart type within 1000 milliseconds and without requiring a page reload.
3. WHEN a trader selects a chart type, THE Workspace_Store SHALL persist the selected chart type for the active symbol within 1000 milliseconds of selection.
4. WHEN a symbol's chart is loaded, THE Chart_Type_Engine SHALL apply the persisted chart type for that symbol, or candlestick when no chart type has been persisted for that symbol.
5. WHERE a chart type requires configuration parameters (Renko box size, Point & Figure box size and reversal, Kagi reversal, Line Break count), THE Chart_Type_Engine SHALL expose those parameters in a settings control accepting values within the range 1 to 999,999, and WHEN a trader applies updated parameter values within that range, THE Chart_Type_Engine SHALL re-render using the updated values within 1000 milliseconds.
6. IF a trader applies a configuration parameter value that is non-numeric, less than 1, or greater than 999,999, THEN THE Chart_Type_Engine SHALL reject the value, retain the last valid parameter values and rendered chart, and display an error indication identifying the invalid parameter.
7. WHEN computing Heikin Ashi candles, THE Chart_Type_Engine SHALL derive each candle from the standard formula such that each Heikin Ashi close equals the arithmetic average of the source candle's open, high, low, and close values.
8. IF the candle dataset is empty, THEN THE Chart_Type_Engine SHALL display a loading or empty-state message instead of rendering an empty chart frame.
9. IF the candle dataset fails to load, THEN THE Chart_Type_Engine SHALL retain the previously rendered chart and display an error indication that data retrieval failed.

### Requirement 2: Overlay Indicators

**User Story:** As a trader, I want price-overlay indicators, so that I can identify trend, volatility, and mean-reversion levels directly on the price chart.

#### Acceptance Criteria

1. THE Indicator_Engine SHALL provide the following Overlay_Indicators: Simple Moving Average, Exponential Moving Average, Weighted Moving Average, Bollinger Bands, VWAP, Ichimoku Cloud, SuperTrend, Parabolic SAR, Donchian Channel, and Keltner Channel.
2. WHEN a trader adds an Overlay_Indicator whose configured period (1 to 5,000 candles, inclusive) does not exceed the number of candles available in the active series, THE Chart_Renderer SHALL draw the indicator on the price scale aligned to the time axis of the price series within 500 milliseconds of the add action.
3. WHEN a trader configures an Overlay_Indicator parameter to a value within its valid range (period: integer 1 to 5,000 inclusive; Bollinger standard-deviation multiplier: 0.1 to 10.0 inclusive), THE Indicator_Engine SHALL recompute the indicator using the updated parameter values within 500 milliseconds of the configuration action.
4. WHEN the Indicator_Engine completes recomputation of an Overlay_Indicator following a parameter change, THE Chart_Renderer SHALL redraw the indicator using the recomputed values within 200 milliseconds of recomputation completion.
5. IF a trader configures an Overlay_Indicator parameter to a value outside its valid range or of an invalid type, THEN THE Indicator_Engine SHALL reject the change, retain the last valid parameter values and their corresponding plotted output, and produce an error indication identifying the rejected parameter.
6. IF a trader adds or configures an Overlay_Indicator whose required period exceeds the number of candles available in the active series, THEN THE Indicator_Engine SHALL omit the indicator computation and THE Chart_Renderer SHALL display an indication that insufficient data is available, without altering the price series rendering.
7. WHEN a new live candle is appended to the active series, THE Indicator_Engine SHALL update each active Overlay_Indicator to include the new candle within 250 milliseconds of the append event.
8. WHERE an Overlay_Indicator produces multiple plotted lines or a filled band (for example, Bollinger Bands or Ichimoku Cloud), THE Chart_Renderer SHALL render every constituent line and fill defined by that indicator.
9. THE Indicator_Engine SHALL compute the Exponential Moving Average using the standard smoothing factor of 2 / (period + 1).

### Requirement 3: Oscillator Indicators in Sub-Panes

**User Story:** As a trader, I want oscillator indicators in dedicated panes, so that I can read momentum and volume signals without obscuring price.

#### Acceptance Criteria

1. THE Indicator_Engine SHALL provide the following Oscillator_Indicators: RSI, MACD, Stochastic, ADX/DMI, ATR, OBV, CCI, MFI, and Williams %R.
2. WHEN a trader adds an Oscillator_Indicator, THE Pane_Manager SHALL render the indicator in an Indicator_Pane positioned below the price pane and below any existing Indicator_Panes, stacked in order of addition from top to bottom.
3. THE Pane_Manager SHALL synchronize every Indicator_Pane with the price pane such that the time axis bounds and the visible time range of each Indicator_Pane are identical to those of the price pane.
4. WHEN a trader pans or zooms the price pane, THE Pane_Manager SHALL apply the same visible time range to every Indicator_Pane within one Frame_Budget of the price pane update.
5. WHERE an Oscillator_Indicator defines reference levels (for example, RSI 30/70 or Stochastic 20/80), THE Chart_Renderer SHALL draw those reference levels in the Indicator_Pane.
6. WHEN a trader removes an Oscillator_Indicator and no other indicators remain in its Indicator_Pane, THE Pane_Manager SHALL remove the empty Indicator_Pane and distribute its vertical space proportionally among the remaining panes so that the sum of all pane heights equals the chart's available vertical height with no unallocated gap.
7. WHEN a new live candle is appended to the active series, THE Indicator_Engine SHALL update each active Oscillator_Indicator to include the new candle within one Frame_Budget of the candle append.
8. IF the active series contains fewer candles than the minimum lookback period required to compute an active Oscillator_Indicator, THEN THE Indicator_Engine SHALL retain the Indicator_Pane without plotting indicator values and indicate that insufficient data is available to compute the indicator.

### Requirement 4: Indicator Management

**User Story:** As a trader, I want to manage my active indicators, so that I can control which signals appear and how they look.

#### Acceptance Criteria

1. WHEN a trader opens the Indicator_Manager, THE Indicator_Manager SHALL display a list of all available Overlay_Indicators and Oscillator_Indicators within 1 second.
2. WHEN a trader enters text in the Indicator_Manager search field, THE Indicator_Manager SHALL display only the indicators whose names contain the entered text (case-insensitive) within 500 milliseconds of the last keystroke.
3. WHEN a trader adds an indicator from the Indicator_Manager, THE Indicator_Manager SHALL add the indicator to the active-indicator list for the current symbol and the active indicator count for that symbol SHALL not exceed 50.
4. IF a trader attempts to add an indicator that is already present in the active-indicator list for the current symbol, THEN THE Indicator_Manager SHALL reject the addition, retain the existing active-indicator list unchanged, and display an indication that the indicator is already active.
5. IF a trader attempts to add an indicator when the active-indicator list for the current symbol already contains 50 indicators, THEN THE Indicator_Manager SHALL reject the addition, retain the existing active-indicator list unchanged, and display an indication that the maximum indicator limit is reached.
6. WHEN a trader removes an indicator, THE Chart_Renderer SHALL remove the indicator's rendered output from the chart within 500 milliseconds.
7. WHEN a trader toggles an active indicator's visibility, THE Chart_Renderer SHALL show or hide that indicator's rendered output within 500 milliseconds while retaining the indicator's configuration.
8. WHEN a trader changes an active indicator's color or line style, THE Chart_Renderer SHALL redraw the indicator using the updated style within 500 milliseconds.
9. WHEN a trader adds, removes, configures, or restyles an indicator, THE Workspace_Store SHALL persist the active-indicator list and settings for the current symbol within 2 seconds.
10. WHEN a symbol's chart is loaded, THE Indicator_Manager SHALL restore the persisted active-indicator list and settings for that symbol within 2 seconds.
11. IF no persisted active-indicator list exists for the loaded symbol, THEN THE Indicator_Manager SHALL initialize an empty active-indicator list for that symbol.

### Requirement 5: Drawing Tools

**User Story:** As a trader, I want a complete set of working drawing tools, so that I can mark up levels, trends, and patterns on the chart.

#### Acceptance Criteria

1. THE Drawing_Engine SHALL support creating the following drawing categories: trend lines (trend line, ray, horizontal line, horizontal ray, vertical line, cross line, extended line), channels (parallel channel, regression trend, flat top/bottom, disjoint channel), Fibonacci tools (retracement, extension, fib channel, fib time zone), shapes (rectangle, circle, ellipse, triangle, path, polyline), text and notes, and projection tools (long position, short position, price range, date range, date-and-price range).
2. WHEN a trader selects a drawing tool and clicks the required number of anchor points (1 anchor for single-point tools such as horizontal line, vertical line, and text; 2 anchors for two-point tools such as trend line, rectangle, and Fibonacci retracement; 3 or more anchors for multi-point tools such as parallel channel, path, and polyline), THE Drawing_Engine SHALL create the drawing anchored to the clicked time and price coordinates and render it within 100 milliseconds.
3. IF a trader begins a drawing but cancels before placing the required number of anchor points, THEN THE Drawing_Engine SHALL discard the partial anchors and SHALL NOT create a drawing.
4. WHEN the chart is panned or zoomed, THE Drawing_Engine SHALL reposition every drawing within 100 milliseconds so that each drawing remains anchored to its original time and price coordinates with a positional deviation of no more than 1 pixel.
5. WHILE a drawing is unlocked, WHEN a trader selects it and drags an anchor point, THE Drawing_Engine SHALL update the drawing's geometry to the new time and price coordinates.
6. WHILE magnet mode is enabled, THE Drawing_Engine SHALL snap each newly placed or dragged anchor point to the nearest open, high, low, or close value of the candle closest to the pointer when that value is within 10 pixels of the pointer, and SHALL otherwise place the anchor at the exact pointer coordinates.
7. WHILE a drawing is locked, IF a trader attempts to modify or delete it, THEN THE Drawing_Engine SHALL reject the change, retain the drawing's geometry, and present a visible indication that the drawing is locked.
8. WHEN a trader toggles drawing visibility off, THE Drawing_Engine SHALL hide all drawings while retaining them in state.
9. WHEN a trader clears drawings and confirms the action, THE Drawing_Engine SHALL remove all unlocked user-created drawings for the active symbol while retaining locked drawings.
10. WHEN a Fibonacci retracement is drawn between two price anchors, THE Drawing_Engine SHALL render horizontal levels at the 0, 0.236, 0.382, 0.5, 0.618, 0.786, and 1.0 ratios of the anchored price range.
11. WHEN a trader creates, edits, or deletes a drawing, THE Workspace_Store SHALL persist the drawing set for the current symbol within 1 second.
12. IF persisting the drawing set fails, THEN THE Workspace_Store SHALL retain the last successfully saved drawing set and present an indication that the drawings could not be saved.

### Requirement 6: Professional Footprint Chart

**User Story:** As an order-flow trader, I want a professional footprint chart, so that I can read bid/ask activity, imbalances, and delta at each price level.

#### Acceptance Criteria

1. WHILE the chart mode is FOOTPRINT, THE Footprint_Engine SHALL render, for each candle, a per-price-level cluster showing bid volume and ask volume grouped by Tick_Size.
2. WHERE live order-flow ticks exist for a candle, THE Footprint_Engine SHALL build that candle's cluster from the live order-flow data.
3. IF no live order-flow ticks exist for a candle, THEN THE Footprint_Engine SHALL build that candle's cluster from a synthetic bid/ask distribution and present a visible indication that the cluster is synthetic.
4. THE Footprint_Engine SHALL compute and display per-candle Delta as the signed value of ask volume minus bid volume.
5. THE Footprint_Engine SHALL compute and display Cumulative_Delta as a running sum of per-candle Delta beginning at zero from the leftmost visible candle.
6. THE Footprint_Engine SHALL highlight each price level that qualifies as an Imbalance, where an Imbalance occurs when the ratio of the larger to the smaller of the diagonally-opposed bid and ask volumes is greater than or equal to a configurable ratio that defaults to 3:1 and accepts values from 1.5:1 to 20:1.
7. THE Footprint_Engine SHALL identify and visually mark the POC price level within each candle, defined as the price level with the greatest total volume, breaking ties by selecting the level closest to the candle's close.
8. THE Footprint_Engine SHALL render a per-candle footer summarizing that candle's total volume and Delta.
9. WHEN a trader changes the Tick_Size to a value greater than zero, THE Footprint_Engine SHALL regroup price levels and re-render using the new Tick_Size within one Frame_Budget.
10. WHEN a new order-flow tick or candle arrives, THE Footprint_Engine SHALL recompute the affected candle's cluster, Delta, Cumulative_Delta, Imbalance, and POC, and re-render within one Frame_Budget.
11. IF no candle data is available, THEN THE Footprint_Engine SHALL display a waiting-for-data message and SHALL NOT render any clusters.

### Requirement 7: Professional Volume Profile

**User Story:** As a trader, I want a configurable volume profile, so that I can see where the most volume traded and locate high-probability support and resistance.

#### Acceptance Criteria

1. THE Volume_Profile_Engine SHALL support exactly three profile ranges: visible range, session, and fixed range.
2. THE Volume_Profile_Engine SHALL bin traded volume into a configurable number of price-level rows, defaulting to 24 rows and accepting values from 1 to 1000 rows, and SHALL render one horizontal volume bar per row aligned to the price scale across the selected profile range.
3. THE Volume_Profile_Engine SHALL compute and mark the POC, defined as the single price-level row with the greatest total traded volume, for the selected profile range.
4. THE Volume_Profile_Engine SHALL compute the Value_Area as the contiguous set of price-level rows around the POC whose cumulative volume reaches a configurable percentage of total volume, where the percentage defaults to 70 percent and accepts integer values from 1 to 100 percent, and SHALL render the VAH and VAL boundaries at the upper and lower price edges of that set.
5. WHERE the profile range is visible range, WHEN the trader pans or zooms the chart and no further pan or zoom event occurs for 200 milliseconds, THE Volume_Profile_Engine SHALL recompute the profile for the new visible range within one Frame_Budget.
6. WHERE the profile range is fixed range, WHEN the trader defines the range by selecting a start anchor and an end anchor, THE Volume_Profile_Engine SHALL compute the profile only over the inclusive price-time span between the two anchors.
7. THE Volume_Profile_Engine SHALL render Value_Area bars in a visual style distinct from non-Value_Area bars such that the two sets are differentiable by at least one observable visual attribute.
8. WHERE developing-value-area display is enabled, WHEN each new session data interval is processed, THE Volume_Profile_Engine SHALL recompute and render the developing Value_Area, POC, VAH, and VAL for the data accumulated up to that interval.
9. IF the selected profile range contains zero traded volume, THEN THE Volume_Profile_Engine SHALL render an empty-profile indication and SHALL NOT display POC, VAH, or VAL markers.
10. IF the fixed range end anchor is positioned at or before the start anchor, THEN THE Volume_Profile_Engine SHALL reject the range, retain the previously computed profile unchanged, and present an indication that the anchor selection is invalid.

### Requirement 8: Trading Strategies and Strategy Overlays

**User Story:** As a trader, I want to apply trading strategies to the chart, so that I can see entry and exit signals generated by rule-based logic.

#### Acceptance Criteria

1. THE Strategy_Engine SHALL provide a selectable list of at least three rule-based strategies, including a moving-average crossover strategy, an RSI mean-reversion strategy, and a breakout strategy.
2. WHEN a trader applies a strategy and the loaded candle data contains at least the strategy's required lookback count of candles, THE Strategy_Engine SHALL evaluate the strategy over the loaded candle data and produce a set of zero or more entry and exit signals, where each signal includes a timestamp and a price, within 2 seconds of the apply action.
3. IF a trader applies a strategy while the loaded candle data contains fewer candles than the strategy's required lookback count, THEN THE Strategy_Engine SHALL produce no signals and SHALL provide an indication that the available data is insufficient to evaluate the strategy.
4. WHEN the Strategy_Engine produces one or more signals, THE Chart_Renderer SHALL render an entry or exit marker anchored to each signal's candle within 1 second of the signals becoming available.
5. WHEN a trader configures a strategy parameter to a value within its defined valid range, THE Strategy_Engine SHALL re-evaluate the strategy within 2 seconds and THE Chart_Renderer SHALL re-render the resulting signals within 1 second of re-evaluation completing.
6. IF a trader configures a strategy parameter to a value outside its defined valid range, THEN THE Strategy_Engine SHALL reject the change, retain the previously applied parameter value and its existing signals, and provide an error indication identifying the invalid parameter.
7. WHEN a new live candle closes, THE Strategy_Engine SHALL evaluate the strategy against the closed candle and append any newly produced signal within 2 seconds of the candle close.
8. WHEN a trader removes an applied strategy, THE Chart_Renderer SHALL remove all of that strategy's markers from the chart within 1 second.
9. WHERE a strategy reports summary performance over the loaded data, THE Strategy_Engine SHALL expose the total signal count and the net result, expressed as a numeric value, for display.

### Requirement 9: Real-Time Updates and Animation Performance

**User Story:** As an active trader, I want smooth, responsive chart updates, so that the chart keeps pace with the market without stutter.

#### Acceptance Criteria

1. WHILE the user pans or zooms the chart with a dataset of up to 5,000 candles, THE Chart_Renderer SHALL complete each render pass within the Frame_Budget for at least 95% of frames during the interaction.
2. WHEN a live OHLC update arrives from the Realtime_Feed for the active symbol and Timeframe, THE Chart_Renderer SHALL reflect the update on the chart within 200 milliseconds of the update being received by the store.
3. WHEN a live OHLC update modifies only the most recent candle, THE Chart_Renderer SHALL update the existing candle in place without replacing or re-rendering the remaining candles in the dataset.
4. WHEN the active symbol changes, THE Chart_Renderer SHALL clear the previous symbol's rendered series before rendering the new symbol's data, such that no candle from the previous symbol remains visible.
5. WHEN a new live candle is appended AND the right edge of the visible time axis was showing the most recent candle before the append, THE Chart_Renderer SHALL keep the newly appended candle visible at the right edge of the time axis.
6. IF a live update arrives with a timestamp earlier than the last rendered candle, THEN THE Chart_Renderer SHALL repaint from the store's ordered dataset (candles sorted ascending by timestamp with no duplicate timestamps) and SHALL complete this repaint without raising an unhandled error.
7. WHILE the Realtime_Feed is disconnected, THE Charting_Suite SHALL continue to display the last received dataset unchanged AND SHALL display a persistent visible indicator of the disconnected state within 2 seconds of detecting the disconnection.
8. WHEN the Realtime_Feed transitions from disconnected to connected, THE Charting_Suite SHALL remove the disconnected-state indicator within 2 seconds of detecting the reconnection.

### Requirement 10: Crosshair, Readouts, and Interaction

**User Story:** As a trader, I want precise crosshair readouts synchronized across panes, so that I can inspect exact values at any point in time.

#### Acceptance Criteria

1. WHEN the trader moves the crosshair over the price pane and a candle exists at the crosshair's time position, THE Crosshair_Controller SHALL display the open, high, low, and close values of that candle, each formatted to the instrument's configured decimal precision, within 100 milliseconds of the crosshair coming to rest.
2. WHEN the trader moves the crosshair to a time position where an active indicator has a defined value, THE Crosshair_Controller SHALL display that indicator's value at the crosshair's time position, formatted to the instrument's configured decimal precision.
3. IF the trader moves the crosshair to a time position where an active indicator has no defined value (for example, before the indicator's warm-up period completes), THEN THE Crosshair_Controller SHALL display a no-value placeholder for that indicator rather than a numeric value.
4. WHEN the trader moves the crosshair in any pane, THE Crosshair_Controller SHALL render a vertical crosshair at the identical time position in every other pane within 100 milliseconds of the source crosshair moving.
5. WHILE the cursor is positioned over an active drawing or one of its anchors, THE Drawing_Engine SHALL render a hover state that visually differs from the drawing's unhovered state, indicating the drawing is selectable.
6. WHEN the trader scrolls the mouse wheel over the chart, THE Chart_Renderer SHALL zoom the time axis centered on the cursor's time position, constraining the zoom level so that no fewer than 5 candles and no more than 5,000 candles are visible.
7. WHILE the eraser cursor is active and the trader clicks on a drawing, THE Drawing_Engine SHALL delete the clicked drawing and remove its rendered representation from all panes within 100 milliseconds of the click.
8. IF the trader moves the crosshair to a time position outside the loaded candle data range, THEN THE Crosshair_Controller SHALL display a no-value placeholder for the OHLC readout rather than values from an adjacent candle.

### Requirement 11: Workspace Persistence and Layout

**User Story:** As a trader, I want my chart setup to persist, so that my indicators, drawings, and layout return when I reopen a symbol or restart the app.

#### Acceptance Criteria

1. WHEN a trader modifies the chart type, an indicator, a drawing, or the pane layout for the active symbol, THE Workspace_Store SHALL persist the complete current workspace state for that symbol within 2 seconds of the most recent change.
2. WHEN a trader opens a symbol that has a persisted workspace, THE Workspace_Store SHALL restore that symbol's chart type, active indicators, indicator settings, drawings, and pane layout to match the most recently persisted state.
3. WHEN a trader opens a symbol that has no persisted workspace, THE Workspace_Store SHALL apply default settings consisting of a candlestick chart with zero active indicators and zero drawings.
4. IF restoring a persisted workspace fails, THEN THE Workspace_Store SHALL apply the default settings defined in criterion 3, SHALL retain the existing on-screen state rather than discarding it, and SHALL present an indication to the trader that the saved workspace could not be restored.
5. IF persisting a workspace change fails, THEN THE Workspace_Store SHALL retain the in-memory workspace state, SHALL present an indication to the trader that the change could not be saved, and SHALL retry persistence on the next workspace change.
6. WHILE running outside the Tauri runtime, THE Workspace_Store SHALL maintain all workspace state in memory for the duration of the session without raising an error, and SHALL discard that state when the session ends.

### Requirement 12: Chart Surface User Experience

**User Story:** As a trader, I want a clean, premium chart interface, so that I can access tools efficiently without visual clutter.

#### Acceptance Criteria

1. THE Charting_Suite SHALL present, as persistently visible and interactive controls on the chart surface, a chart-type selector, an indicator-manager entry point, the drawing toolbar, the chart-mode toggle (Standard, Volume Profile, Footprint), the Timeframe selector, and a strategy entry point.
2. WHEN a trader opens an indicator or chart-type settings dialog, THE Charting_Suite SHALL display the dialog as an overlay that keeps the underlying chart visible behind it and does not navigate away from the chart.
3. WHEN a trader closes an indicator or chart-type settings dialog, THE Charting_Suite SHALL remove the overlay and restore the chart surface to an interactive state without reloading the chart.
4. WHEN a trader expands the chart to fullscreen, THE Charting_Suite SHALL resize the chart and all panes to occupy 100% of the available viewport width and height within 1 second.
5. IF a fullscreen expansion request fails or is not supported by the display environment, THEN THE Charting_Suite SHALL retain the chart at its current (pre-request) dimensions and present an indication that fullscreen is unavailable.
6. THE Charting_Suite SHALL render chart text and lines at the display's device pixel ratio for device pixel ratios from 1.0 to 4.0 so that text and lines remain rendered without scaling-induced blur on high-DPI screens.
7. WHERE the active instrument is an index with zero reported volume, THE Charting_Suite SHALL display a label indicating that the shown volume is a price-range proxy rather than reported traded volume.
