// Feature: professional-charting-suite
//
// StrategyEngine — pure, rule-based trading-strategy evaluation.
//
// This module is a deterministic, side-effect-free computation layer mirroring
// the IndicatorEngine design. Each strategy is described by a `StrategyDef`
// that carries its valid parameter ranges (`paramSpec`), defaults, a
// `requiredLookback(params)` count, a pure `evaluate(candles, params)` that
// produces entry/exit `Signal[]` anchored to candle timestamps, and a
// `summarize(signals, candles)` that exposes a total signal count and a numeric
// net result for display.
//
// The shared `STRATEGY_REGISTRY` ships at least three rule-based strategies:
// moving-average crossover, RSI mean-reversion, and breakout (Requirement 8.1).
//
// Relevant requirements:
//  - 8.1 provide >= 3 selectable strategies incl. MA-cross, RSI mean-reversion,
//        breakout
//  - 8.2 evaluate over loaded candles producing zero or more entry/exit
//        signals, each with a timestamp and a price
//  - 8.3 fewer candles than required lookback => no signals (evaluate -> [])
//  - 8.5/8.6 strategy parameters have a declared valid range (`paramSpec`) so
//        the caller can validate via `validateParams`
//  - 8.9 expose total signal count and a numeric net result (`summarize`)

import type { ChartCandle, NumericRange } from '../types';

// ---------------------------------------------------------------------------
// Signal / definition shapes
// ---------------------------------------------------------------------------

/** The four signal kinds a rule-based strategy can emit. */
export type SignalKind = 'entry-long' | 'exit-long' | 'entry-short' | 'exit-short';

/**
 * A single strategy signal anchored to a candle.
 *
 * `time` is the timestamp of the candle the signal fires on (so the renderer
 * can place a marker on that candle), `price` is the numeric price the signal
 * is recorded at (the candle close), and `kind` is the entry/exit direction.
 */
export interface Signal {
  time: number;
  price: number;
  kind: SignalKind;
}

/** A bag of numeric strategy parameters, e.g. `{ fast: 9, slow: 21 }`. */
export interface StrategyParams {
  [key: string]: number;
}

/** The result of summarizing a strategy's signals over the loaded data. */
export interface StrategySummary {
  /** Total number of produced signals. */
  count: number;
  /** Net result over completed long/short round trips, a finite number. */
  netResult: number;
}

/** A registered strategy: metadata + validation spec + pure evaluation. */
export interface StrategyDef {
  /** Stable id, e.g. `'ma-cross' | 'rsi-mean-reversion' | 'breakout'`. */
  id: string;
  name: string;
  /** Default parameter values applied when a caller omits a parameter. */
  defaults: StrategyParams;
  /** Valid range per configurable parameter (used by `validateParams`). */
  paramSpec: Record<string, NumericRange>;
  /** Minimum number of candles required to evaluate the strategy. */
  requiredLookback(params: StrategyParams): number;
  /** Pure transform from candles + params to entry/exit signals. */
  evaluate(candles: ChartCandle[], params: StrategyParams): Signal[];
  /** Total signal count and net numeric result over the produced signals. */
  summarize(signals: Signal[], candles: ChartCandle[]): StrategySummary;
}

// ---------------------------------------------------------------------------
// Shared numeric ranges
// ---------------------------------------------------------------------------

/** Period/lookback range shared by every window-based parameter. */
const PERIOD: NumericRange = { min: 1, max: 5_000, integer: true };
/** RSI threshold range (0..100). */
const RSI_LEVEL: NumericRange = { min: 0, max: 100, integer: false };

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/** Merge caller params over a strategy's defaults. */
function withDefaults(defaults: StrategyParams, params: StrategyParams): StrategyParams {
  return { ...defaults, ...params };
}

function closes(candles: ChartCandle[]): number[] {
  return candles.map((c) => c.close);
}

type Series = (number | null)[];

/** Simple moving average over a rolling window, aligned to the candle index. */
function smaSeries(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

/**
 * Wilder's RSI aligned to the candle index. The first defined value is at
 * index `period` (the seed average of the first `period` gains/losses), after
 * which Wilder's smoothing applies. A zero average loss yields RSI 100.
 */
function rsiSeries(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0 || values.length <= period) return out;

  let gainSum = 0;
  let lossSum = 0;
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1];
    if (change >= 0) gainSum += change;
    else lossSum -= change;
  }
  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;
  out[period] = rsiFromAverages(avgGain, avgLoss);

  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1];
    const gain = change >= 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = rsiFromAverages(avgGain, avgLoss);
  }
  return out;
}

function rsiFromAverages(avgGain: number, avgLoss: number): number {
  if (avgLoss === 0) return avgGain === 0 ? 50 : 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

/** Highest high over the `period` candles ending at `i - 1` (prior window). */
function priorHighestHigh(candles: ChartCandle[], i: number, period: number): number {
  let hh = -Infinity;
  for (let k = 1; k <= period; k++) hh = Math.max(hh, candles[i - k].high);
  return hh;
}

/** Lowest low over the `period` candles ending at `i - 1` (prior window). */
function priorLowestLow(candles: ChartCandle[], i: number, period: number): number {
  let ll = Infinity;
  for (let k = 1; k <= period; k++) ll = Math.min(ll, candles[i - k].low);
  return ll;
}

/**
 * Net result over completed round trips. Walks the chronologically-ordered
 * signals maintaining at most one open position: `entry-long`/`entry-short`
 * open a position (when flat), and the matching exit closes it, accruing
 * `exit - entry` for longs and `entry - exit` for shorts. Only finite price
 * deltas are accrued so the result is always a finite number (Requirement 8.9,
 * Property 27).
 */
function netFromSignals(signals: Signal[]): number {
  let net = 0;
  let openSide: 'long' | 'short' | null = null;
  let openPrice = 0;

  for (const s of signals) {
    if (s.kind === 'entry-long' || s.kind === 'entry-short') {
      if (openSide === null && Number.isFinite(s.price)) {
        openSide = s.kind === 'entry-long' ? 'long' : 'short';
        openPrice = s.price;
      }
    } else if (s.kind === 'exit-long' && openSide === 'long') {
      const delta = s.price - openPrice;
      if (Number.isFinite(delta)) net += delta;
      openSide = null;
    } else if (s.kind === 'exit-short' && openSide === 'short') {
      const delta = openPrice - s.price;
      if (Number.isFinite(delta)) net += delta;
      openSide = null;
    }
  }
  return Number.isFinite(net) ? net : 0;
}

/** Shared summarize: signal count plus the net round-trip result. */
function summarizeSignals(signals: Signal[]): StrategySummary {
  return { count: signals.length, netResult: netFromSignals(signals) };
}

// ---------------------------------------------------------------------------
// Strategy definitions
// ---------------------------------------------------------------------------

/**
 * Moving-average crossover. Emits `entry-long` when the fast SMA crosses above
 * the slow SMA and `exit-long` when it crosses back below. Requires `slow`
 * candles before any SMA pair is defined.
 */
const MA_CROSS_DEF: StrategyDef = {
  id: 'ma-cross',
  name: 'MA Crossover',
  defaults: { fast: 9, slow: 21 },
  paramSpec: { fast: PERIOD, slow: PERIOD },
  requiredLookback: (p) => Math.max(p.fast, p.slow),
  evaluate(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.requiredLookback(p)) return [];
    const src = closes(candles);
    const fast = smaSeries(src, p.fast);
    const slow = smaSeries(src, p.slow);
    const signals: Signal[] = [];
    for (let i = 1; i < candles.length; i++) {
      const f0 = fast[i - 1];
      const s0 = slow[i - 1];
      const f1 = fast[i];
      const s1 = slow[i];
      if (f0 === null || s0 === null || f1 === null || s1 === null) continue;
      const c = candles[i];
      if (f0 <= s0 && f1 > s1) {
        signals.push({ time: c.time, price: c.close, kind: 'entry-long' });
      } else if (f0 >= s0 && f1 < s1) {
        signals.push({ time: c.time, price: c.close, kind: 'exit-long' });
      }
    }
    return signals;
  },
  summarize: (signals) => summarizeSignals(signals),
};

/**
 * RSI mean-reversion. Emits `entry-long` when RSI crosses up through the
 * oversold level and `exit-long` when it crosses down through the overbought
 * level. Requires `period + 1` candles before the first RSI value exists.
 */
const RSI_MEAN_REVERSION_DEF: StrategyDef = {
  id: 'rsi-mean-reversion',
  name: 'RSI Mean Reversion',
  defaults: { period: 14, oversold: 30, overbought: 70 },
  paramSpec: { period: PERIOD, oversold: RSI_LEVEL, overbought: RSI_LEVEL },
  requiredLookback: (p) => p.period + 1,
  evaluate(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.requiredLookback(p)) return [];
    const rsi = rsiSeries(closes(candles), p.period);
    const signals: Signal[] = [];
    for (let i = 1; i < candles.length; i++) {
      const r0 = rsi[i - 1];
      const r1 = rsi[i];
      if (r0 === null || r1 === null) continue;
      const c = candles[i];
      if (r0 <= p.oversold && r1 > p.oversold) {
        signals.push({ time: c.time, price: c.close, kind: 'entry-long' });
      } else if (r0 >= p.overbought && r1 < p.overbought) {
        signals.push({ time: c.time, price: c.close, kind: 'exit-long' });
      }
    }
    return signals;
  },
  summarize: (signals) => summarizeSignals(signals),
};

/**
 * Breakout. Emits `entry-long` when a candle closes above the highest high of
 * the prior `lookback` candles and `exit-long` when it closes below the lowest
 * low of the prior `lookback` candles. Requires `lookback + 1` candles so a
 * full prior window exists before the current candle.
 */
const BREAKOUT_DEF: StrategyDef = {
  id: 'breakout',
  name: 'Breakout',
  defaults: { lookback: 20 },
  paramSpec: { lookback: PERIOD },
  requiredLookback: (p) => p.lookback + 1,
  evaluate(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.requiredLookback(p)) return [];
    const signals: Signal[] = [];
    for (let i = p.lookback; i < candles.length; i++) {
      const c = candles[i];
      const hh = priorHighestHigh(candles, i, p.lookback);
      const ll = priorLowestLow(candles, i, p.lookback);
      if (c.close > hh) {
        signals.push({ time: c.time, price: c.close, kind: 'entry-long' });
      } else if (c.close < ll) {
        signals.push({ time: c.time, price: c.close, kind: 'exit-long' });
      }
    }
    return signals;
  },
  summarize: (signals) => summarizeSignals(signals),
};

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/**
 * The shared strategy registry, keyed by strategy id. Ships the three named
 * rule-based strategies (Requirement 8.1) and is typed as an open record so
 * additional strategies can be registered without restructuring this module.
 */
export const STRATEGY_REGISTRY: Record<string, StrategyDef> = {};

/** Register (or replace) a strategy definition in the shared registry. */
export function registerStrategy(def: StrategyDef): void {
  STRATEGY_REGISTRY[def.id] = def;
}

/** Look up a registered strategy definition by id, if present. */
export function getStrategy(id: string): StrategyDef | undefined {
  return STRATEGY_REGISTRY[id];
}

/** The list of strategy ids currently registered, in registration order. */
export function listStrategies(): string[] {
  return Object.keys(STRATEGY_REGISTRY);
}

const STRATEGY_DEFS: StrategyDef[] = [
  MA_CROSS_DEF,
  RSI_MEAN_REVERSION_DEF,
  BREAKOUT_DEF,
];

for (const def of STRATEGY_DEFS) registerStrategy(def);
