/**
 * charting/datafeed.ts — TradingView Advanced Charts JS API Datafeed adapter.
 *
 * Bridges the existing Zerodha/Kite data pipeline to the TradingView widget's
 * IBasicDatafeed interface. Reuses the project's existing data-fetching logic:
 *   - Historical candles: Kite Historical API via aggregator proxy `/kite/historical`
 *   - Live ticks: Zustand `useTradeStore.ohlcCandles` or Tauri IPC `ohlc-tick`
 *   - Symbol search: `/kite/quote` endpoint
 *
 * No new backend endpoints are needed — this is a pure frontend adapter.
 */

import type {
  IBasicDatafeed,
  OnReadyCallback,
  SearchSymbolsCallback,
  ResolveCallback,
  ErrorCallback,
  HistoryCallback,
  SubscribeBarsCallback,
  Bar,
  LibrarySymbolInfo,
  ResolutionString,
  PeriodParams,
  DatafeedConfiguration,
} from './datafeedTypes';
import { useTradeStore, type OhlcCandle } from '../store/useTradeStore';
import { kiteFetch } from '../lib/tauriFetch';

// ── Resolution Mapping ────────────────────────────────────────────────────
// Maps TV resolution strings to Kite Historical API interval strings.

const RESOLUTION_TO_KITE_INTERVAL: Record<string, string> = {
  '1':    'minute',
  '2':    'minute',
  '3':    '3minute',
  '4':    'minute',
  '5':    '5minute',
  '10':   '10minute',
  '15':   '15minute',
  '30':   '30minute',
  '60':   '60minute',
  '75':   '15minute',
  '120':  '60minute',
  '125':  '15minute',
  '180':  '60minute',
  '240':  '60minute',
  '1D':   'day',
  'D':    'day',
  '1W':   'day',
  'W':    'day',
  '1M':   'day',
  'M':    'day',
};

/**
 * Kite's per-interval day cap for a single `/instruments/historical` request.
 * Asking for a window wider than this returns `[]` with no error, which is
 * exactly what stops scroll-back dead. Source: Kite Historical API docs.
 */
const KITE_INTERVAL_MAX_DAYS: Record<string, number> = {
  minute: 7,
  '3minute': 30,
  '5minute': 30,
  '10minute': 30,
  '15minute': 60,
  '30minute': 60,
  '60minute': 60,
  day: 2000,
};

/** Convert TV resolution to a UI timeframe string for the Tauri IPC. */
export const RESOLUTION_TO_TIMEFRAME: Record<string, string> = {
  '1':   '1m',  '2':   '2m',  '3':   '3m',  '4':   '4m',
  '5':   '5m',  '10':  '10m', '15':  '15m', '30':  '30m',
  '60':  '1h',  '75':  '75m', '120': '2h',  '125': '125m',
  '180': '3h',  '240': '4h',
  '1D':  '1D',  'D':   '1D',
  '1W':  '1W',  'W':   '1W',
  '1M':  '1M',  'M':   '1M',
};

/** All supported resolutions for the symbol info. */
const SUPPORTED_RESOLUTIONS: ResolutionString[] = [
  '1', '2', '3', '4', '5', '10', '15', '30',
  '60', '75', '120', '125', '180', '240',
  '1D', '1W', '1M',
];

// ── Tauri Detection ───────────────────────────────────────────────────────
const isTauri = () =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

// ── In-memory scroll-back cache (per symbol + timeframe) ───────────────────
//
// TradingView calls `getBars` once per scroll-back page with the [from, to]
// window it wants. Without persistence, each call returns ONLY the bars fetched
// that turn — so when TV asks for a window that overlaps a slice we already
// pulled, we hit the network again and TV shows whatever Kite happened to
// return this time (often nothing on a cold page). By merging every fetched
// bar into this map keyed by `SYMBOL::TIMEFRAME`, we can serve any overlapping
// window from memory instantly, and only fetch the missing older slice.
const scrollBackCache = new Map<string, Map<number, Bar>>();

function scrollBackKey(symbol: string, timeframe: string): string {
  return `${symbol.toUpperCase()}::${timeframe}`;
}

function readScrollBackCache(symbol: string, timeframe: string, fromMs: number, toMs: number): Bar[] {
  const store = scrollBackCache.get(scrollBackKey(symbol, timeframe));
  if (!store) return [];
  const out: Bar[] = [];
  for (const bar of store.values()) {
    if (bar.time >= fromMs && bar.time <= toMs) out.push(bar);
  }
  out.sort((a, b) => a.time - b.time);
  return out;
}

function mergeScrollBackCache(symbol: string, timeframe: string, bars: Bar[]): void {
  if (bars.length === 0) return;
  const key = scrollBackKey(symbol, timeframe);
  let store = scrollBackCache.get(key);
  if (!store) {
    store = new Map<number, Bar>();
    scrollBackCache.set(key, store);
  }
  for (const b of bars) store.set(b.time, b);
}

/** Drop every scroll-back cache entry for a symbol (called on symbol change). */
export function invalidateScrollBackCache(symbol: string): void {
  const prefix = `${symbol.toUpperCase()}::`;
  for (const key of scrollBackCache.keys()) {
    if (key.startsWith(prefix)) scrollBackCache.delete(key);
  }
  for (const key of exhaustedWindows.keys()) {
    if (key.startsWith(prefix)) exhaustedWindows.delete(key);
  }
  // Also drop the Tauri IPC cache so the next load re-fetches fresh data.
  for (const key of tauriBarCache.keys()) {
    if (key.startsWith(prefix)) tauriBarCache.delete(key);
  }
  for (const key of tauriBarInflight.keys()) {
    if (key.startsWith(prefix)) tauriBarInflight.delete(key);
  }
}

// ── Exhaustion tracking ────────────────────────────────────────────────────
// Tracks `SYMBOL::TIMEFRAME::fromSec` windows where Kite genuinely returned
// zero candles (confirmed empty, NOT a transient network failure). Only when
// a window is in this set do we tell TradingView `noData: true` — which makes
// TV stop paginating further. Without this, a single network glitch would
// permanently kill scroll-back for the session.
const exhaustedWindows = new Set<string>();

function exhaustionKey(symbol: string, timeframe: string, fromSec: number): string {
  return `${symbol.toUpperCase()}::${timeframe}::${fromSec}`;
}

/** Get the earliest bar time (ms) we've ever seen for a symbol+timeframe. */
function earliestKnownBar(symbol: string, timeframe: string): number | undefined {
  const store = scrollBackCache.get(scrollBackKey(symbol, timeframe));
  if (!store || store.size === 0) return undefined;
  let min = Infinity;
  for (const t of store.keys()) {
    if (t < min) min = t;
  }
  return min === Infinity ? undefined : min;
}

// ── Instrument Token Cache ────────────────────────────────────────────────
const tokenCache = new Map<string, number>();

async function resolveInstrumentToken(symbol: string, exchange: string = 'NSE'): Promise<number | null> {
  const cacheKey = `${exchange}:${symbol}`.toUpperCase();
  const cached = tokenCache.get(cacheKey);
  if (cached) return cached;
  try {
    const res = await kiteFetch(`/quote?i=${exchange}:${encodeURIComponent(symbol)}`);
    if (res.ok) {
      const data = await res.json();
      const quotes = data.quotes as { symbol: string; instrument_token: number }[] | undefined;
      if (quotes && quotes.length > 0) {
        const match = quotes.find((q) => q.symbol.toUpperCase() === symbol.toUpperCase());
        const token = match?.instrument_token ?? quotes[0].instrument_token ?? null;
        if (token) {
          tokenCache.set(cacheKey, token);
          return token;
        }
      }
    }

    const resInst = await kiteFetch(`/instruments?q=${encodeURIComponent(symbol)}&exchange=${encodeURIComponent(exchange)}`);
    if (resInst.ok) {
      const data = await resInst.json();
      const results = data.results as { tradingsymbol: string; instrument_token: number }[] | undefined;
      if (results && results.length > 0) {
        const match = results.find((r) => r.tradingsymbol.toUpperCase() === symbol.toUpperCase());
        const token = match?.instrument_token ?? results[0].instrument_token ?? null;
        if (token) {
          tokenCache.set(cacheKey, token);
          return token;
        }
      }
    }
    return null;
  } catch {
    return null;
  }
}

// ── FNO symbol detection ──────────────────────────────────────────────────
function isFnoSymbol(symbol: string): boolean {
  const s = symbol?.trim()?.toUpperCase();
  if (!s) return false;
  if (s.endsWith('FUT')) return true;
  if ((s.endsWith('CE') || s.endsWith('PE')) && /\d/.test(s)) return true;
  return false;
}

// ── Kite Batch Fetch ──────────────────────────────────────────────────────

interface KiteCandleRaw {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

async function fetchKiteBatch(
  symbol: string,
  interval: string,
  from: Date,
  to: Date,
  exchange: string = 'NSE',
  timeframe?: string,
): Promise<Bar[]> {
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  const parseCandles = (data: { candles?: KiteCandleRaw[] }): Bar[] =>
    (data.candles || [])
      .map((c) => ({
        time: c.time * 1000, // TV expects UTC milliseconds
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      }))
      .filter((c) => c.time > 0 && c.open > 0);

  const maxDays = KITE_INTERVAL_MAX_DAYS[interval] ?? 60;
  const dayMs = 24 * 60 * 60 * 1000;
  const pages: { from: Date; to: Date }[] = [];
  let pageEnd = new Date(to);
  while (pageEnd.getTime() >= from.getTime()) {
    const pageStart = new Date(Math.max(from.getTime(), pageEnd.getTime() - (maxDays - 1) * dayMs));
    pages.push({ from: pageStart, to: pageEnd });
    if (pageStart.getTime() <= from.getTime()) break;
    pageEnd = new Date(pageStart.getTime() - dayMs);
  }

  pages.reverse();

  // ── Tauri IPC path (single call, cached) ────────────────────────────────
  // For FNO symbols the Tauri backend (QuestDB + Kite intraday loader) is the
  // SOLE data source — the REST `/kite/historical` proxy cannot resolve FNO
  // instrument tokens and always returns 0 candles. For equity symbols, Tauri
  // seeds the result and REST extends beyond the QuestDB cache edge.
  const symbolIsFno = isFnoSymbol(symbol);
  let tauriBars: Bar[] = [];

  if (isTauri() && timeframe) {
    try {
      const allBars = await fetchTauriBars(symbol, timeframe);
      if (allBars.length > 0) {
        const fromMs = from.getTime();
        const toMs = to.getTime();
        tauriBars = allBars.filter((b) => b.time >= fromMs && b.time <= toMs);
      }
    } catch (err) {
      console.warn('[Datafeed] Tauri IPC get_historical_view failed:', err);
    }
  }

  // For FNO symbols, skip REST entirely — Tauri is the only source that works.
  if (symbolIsFno) {
    return tauriBars;
  }

  // ── Kite Historical REST pages (equity symbols only) ────────────────────
  const all: Bar[] = [...tauriBars];
  for (const page of pages) {
    const dateParams = `&from=${fmt(page.from)}&to=${fmt(page.to)}`;

    try {
      let candles: Bar[] = [];

      const url = `/historical?symbol=${encodeURIComponent(symbol)}&interval=${interval}${dateParams}`;
      const response = await kiteFetch(url);
      if (response.ok) {
        const data = await response.json();
        candles = parseCandles(data);
      }

      if (candles.length === 0) {
        const token = await resolveInstrumentToken(symbol, exchange);
        if (!token) break;
        const tokenUrl = `/historical?instrument_token=${token}&interval=${interval}${dateParams}`;
        const tokenResponse = await kiteFetch(tokenUrl);
        if (!tokenResponse.ok) break;
        const tokenData = await tokenResponse.json();
        candles = parseCandles(tokenData);
      }

      if (candles.length === 0) break;
      all.push(...candles);
    } catch (err) {
      console.warn('[Datafeed] Kite page fetch failed:', err);
      break;
    }
  }

  return all;
}

// ── Tauri IPC Historical Fetch (cached, deduplicated) ─────────────────────
//
// The Tauri `get_historical_view` command returns ALL bars in QuestDB for a
// (symbol, timeframe) pair — typically the full 30-day intraday lookback.
// Calling it on every `getBars` is expensive because the backend re-runs the
// Kite intraday loader each time (~1.4s). We cache the result per
// (symbol, timeframe) and deduplicate concurrent calls via an inflight map.

const tauriBarCache = new Map<string, { bars: Bar[]; fetchedAt: number }>();
const tauriBarInflight = new Map<string, Promise<Bar[]>>();

let tauriPoolReady = false;

async function fetchTauriBars(
  symbol: string,
  timeframe: string,
): Promise<Bar[]> {
  if (!isTauri()) return [];

  const cacheKey = scrollBackKey(symbol, timeframe);

  // Serve from cache if fetched within the last 60 seconds.
  const cached = tauriBarCache.get(cacheKey);
  if (cached && Date.now() - cached.fetchedAt < 60_000) {
    return cached.bars;
  }

  // Deduplicate: if a fetch for this key is already in flight, await it.
  const inflight = tauriBarInflight.get(cacheKey);
  if (inflight) return inflight;

  const promise = (async (): Promise<Bar[]> => {
    try {
      const tauri = await import('@tauri-apps/api/core');

      if (!tauriPoolReady) {
        const start = Date.now();
        while (Date.now() - start < 8000) {
          try {
            tauriPoolReady = await tauri.invoke<boolean>('get_pool_status');
            if (tauriPoolReady) break;
          } catch { /* pool not ready yet */ }
          await new Promise((r) => setTimeout(r, 500));
        }
        if (!tauriPoolReady) return [];
      }

      const response = await tauri.invoke<number[] | Uint8Array>(
        'get_historical_view',
        { symbol, timeframe },
      );
      const buffer =
        response instanceof Uint8Array ? response : new Uint8Array(response);

      const bars = parseBincodeCandles(buffer);
      tauriBarCache.set(cacheKey, { bars, fetchedAt: Date.now() });
      return bars;
    } catch (err) {
      console.warn('[Datafeed] Tauri IPC fetch failed:', err);
      return [];
    } finally {
      tauriBarInflight.delete(cacheKey);
    }
  })();

  tauriBarInflight.set(cacheKey, promise);
  return promise;
}

/** Parse bincode-serialized BinaryCandle structs (48 bytes each). */
function parseBincodeCandles(buffer: Uint8Array): Bar[] {
  const bars: Bar[] = [];
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  const length = Number(view.getBigUint64(0, true));
  let offset = 8;
  for (let i = 0; i < length; i++) {
    const tsMicro = Number(view.getBigInt64(offset, true));
    const open = view.getFloat64(offset + 8, true);
    const high = view.getFloat64(offset + 16, true);
    const low = view.getFloat64(offset + 24, true);
    const close = view.getFloat64(offset + 32, true);
    const volume = Number(view.getBigInt64(offset + 40, true));
    bars.push({
      time: Math.floor(tsMicro / 1000), // microseconds → milliseconds
      open, high, low, close, volume,
    });
    offset += 48;
  }
  return bars;
}

// ── Live Subscription Manager ─────────────────────────────────────────────

interface LiveSubscription {
  symbol: string;
  resolution: string;
  onTick: SubscribeBarsCallback;
  unsubscribe: () => void;
}

const activeSubscriptions = new Map<string, LiveSubscription>();

function startLiveSubscription(
  symbol: string,
  resolution: string,
  onTick: SubscribeBarsCallback,
  listenerGuid: string,
): void {
  const symbolUpper = symbol.toUpperCase();
  let lastBarTime = 0;
  let tickCount = 0;

  const forwardCandle = (candle: { symbol: string; start_timestamp_ms: number; open: number; high: number; low: number; close: number; volume?: number }) => {
    if (candle.symbol.toUpperCase() !== symbolUpper) return;
    const barTimeMs = candle.start_timestamp_ms;
    if (barTimeMs >= lastBarTime) {
      lastBarTime = barTimeMs;
      tickCount++;
      if (tickCount <= 5) {
        console.log(`[Datafeed] Live tick #${tickCount} for ${symbolUpper}: time=${barTimeMs} O=${candle.open} H=${candle.high} L=${candle.low} C=${candle.close}`);
      }
      onTick({
        time: barTimeMs,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume ?? 0,
      });
    }
  };

  // ── Path 1: Zustand store subscription ────────────────────────────────
  // Fires whenever ohlcCandles changes (works for both browser WS and Tauri IPC paths).
  const unsub = useTradeStore.subscribe((state) => {
    const candles = state.ohlcCandles;
    const matching = candles.filter(
      (c) => c.symbol.toUpperCase() === symbolUpper,
    );
    if (matching.length === 0) return;
    forwardCandle(matching[matching.length - 1]);
  });

  // ── Path 2: Direct Tauri IPC listener (lower latency) ─────────────────
  // Listens for ohlc-tick events directly from the Rust backend, bypassing
  // the Zustand store roundtrip for faster chart updates.
  let unlistenTauri: (() => void) | null = null;
  if (isTauri()) {
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        const unlisten = await listen<{ symbol: string; start_timestamp_ms: number; open: number; high: number; low: number; close: number; volume?: number }>('ohlc-tick', (event) => {
          forwardCandle(event.payload);
        });
        unlistenTauri = unlisten;
      } catch {
        // Not in Tauri context — Zustand path will handle it
      }
    })();
  }

  console.log(`[Datafeed] subscribeBars: ${symbolUpper} (resolution=${resolution}, guid=${listenerGuid.slice(0, 8)}…, tauri=${isTauri()})`);

  activeSubscriptions.set(listenerGuid, {
    symbol, resolution, onTick,
    unsubscribe: () => {
      unsub();
      unlistenTauri?.();
    },
  });
}

// ── REST fallback for symbol search (used outside Tauri or when the local
//    `search_instruments` invoke fails). Mirrors the old REST proxy behaviour:
//    GET /kite/instruments?q=<query>&exchange=<ex> → equity + index rows only.
async function fallbackRestSearch(
  userInput: string,
  exchange: string,
  onResult: SearchSymbolsCallback,
): Promise<void> {
  if (!userInput || userInput.length < 1) {
    onResult([]);
    return;
  }

  const ex = exchange || 'NSE';
  try {
    const res = await kiteFetch(`/instruments?q=${encodeURIComponent(userInput)}&exchange=${encodeURIComponent(ex)}`);
    if (!res.ok) {
      onResult([]);
      return;
    }
    const data = await res.json();
    const results = (data.results || []) as {
      tradingsymbol: string;
      name: string;
      exchange: string;
      instrument_type: string;
    }[];
    onResult(
      results.map((inst) => ({
        symbol: inst.tradingsymbol,
        full_name: `${inst.exchange}:${inst.tradingsymbol}`,
        description: inst.name,
        exchange: inst.exchange,
        ticker: `${inst.exchange}:${inst.tradingsymbol}`,
        type: inst.instrument_type === 'INDEX' ? 'index' : 'stock',
      })),
    );
  } catch {
    onResult([]);
  }
}

// ── Datafeed Implementation ───────────────────────────────────────────────

export function createDatafeed(): IBasicDatafeed {
  return {
    onReady(callback: OnReadyCallback): void {
      // TV requires async callback
      setTimeout(() => {
        const config: DatafeedConfiguration = {
          // Single "ALL" exchange entry so TradingView's exchange filter does
          // NOT pre-filter the results — the user picks any symbol across
          // NSE / BSE / NFO from one flat, global result list.
          exchanges: [
            { value: 'ALL', name: 'All', desc: 'All Exchanges (NSE / BSE / NFO)' },
          ],
          // Single "All" symbol type so the type filter doesn't narrow by
          // stock / index / fno either — one global search across everything.
          symbols_types: [
            { name: 'All', value: 'all' },
          ],
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          supports_marks: false,
          supports_timescale_marks: false,
          supports_time: true,
        };
        callback(config);
      }, 0);
    },

    searchSymbols(
      userInput: string,
      _exchange: string,
      _symbolType: string,
      onResult: SearchSymbolsCallback,
    ): void {
      if (!userInput || userInput.length < 1) {
        onResult([]);
        return;
      }

      const query = userInput.trim();

      // ── Global search across NSE / BSE / NFO via the existing
      // `search_instruments` command. It already returns EQ + Index + FNO
      // (CE/PE/FUT) rows from the SQLite `instruments` + `nfo_instruments`
      // tables, so one invoke returns the full global result set. We map every
      // row into TV's `SearchSymbolResultItem` shape without filtering by
      // exchange or type — the user sees equities, indexes, and F&O contracts
      // in one flat list and can pick any of them.
      if (isTauri()) {
        import('@tauri-apps/api/core')
          .then((tauri) =>
            tauri.invoke<
              Array<
                | { kind: 'EQ'; symbol: string; name: string; exchange: string }
                | {
                    kind: 'FNO';
                    tradingsymbol: string;
                    underlying: string;
                    expiry: string;
                    strike: number | null;
                    optionType: string;
                  }
              >
            >('search_instruments', { query }),
          )
          .then((results) => {
            const items = (results || []).map((r) => {
              if (r.kind === 'EQ') {
                const upper = r.symbol.toUpperCase();
                const isIndex =
                  upper === 'NIFTY' ||
                  upper === 'NIFTY 50' ||
                  upper === 'BANKNIFTY' ||
                  upper === 'NIFTY BANK' ||
                  upper === 'FINNIFTY' ||
                  upper === 'MIDCPNIFTY' ||
                  upper === 'SENSEX';
                return {
                  symbol: r.symbol,
                  full_name: `${r.exchange}:${r.symbol}`,
                  description: r.name,
                  exchange: r.exchange,
                  ticker: `${r.exchange}:${r.symbol}`,
                  type: isIndex ? 'index' : 'stock',
                };
              }
              const desc =
                r.optionType === 'FUT'
                  ? `${r.underlying} FUT (${r.expiry})`
                  : `${r.underlying} ${r.strike ?? ''} ${r.optionType} (${r.expiry})`;
              return {
                symbol: r.tradingsymbol,
                full_name: `NFO:${r.tradingsymbol}`,
                description: desc,
                exchange: 'NFO',
                ticker: `NFO:${r.tradingsymbol}`,
                type: 'fno',
              };
            });
            onResult(items);
          })
          .catch((err) => {
            console.warn('[Datafeed] search_instruments failed:', err);
            fallbackRestSearch(userInput, '', onResult);
          });
        return;
      }

      // ── REST fallback (non-Tauri / browser dev) ──────────────────────────
      fallbackRestSearch(userInput, '', onResult);
    },

    resolveSymbol(
      symbolName: string,
      onResolve: ResolveCallback,
      onError: ErrorCallback,
    ): void {
      // Strip exchange prefix if present (e.g. "NSE:RELIANCE" → "RELIANCE")
      const cleanSymbol = symbolName.includes(':')
        ? symbolName.split(':')[1]
        : symbolName;
      const exchange = symbolName.includes(':')
        ? symbolName.split(':')[0]
        : 'NSE';

      setTimeout(() => {
        const symbolInfo: LibrarySymbolInfo = {
          name: cleanSymbol,
          full_name: `${exchange}:${cleanSymbol}`,
          ticker: `${exchange}:${cleanSymbol}`,
          description: cleanSymbol,
          type: exchange === 'INDICES' ? 'index' : 'stock',
          session: '0915-1530',
          timezone: 'Asia/Kolkata',
          exchange: exchange,
          listed_exchange: exchange,
          format: 'price',
          minmov: 1,
          pricescale: 100, // 2 decimal places (₹XX.XX)
          has_intraday: true,
          has_daily: true,
          has_weekly_and_monthly: true,
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          intraday_multipliers: ['1', '2', '3', '4', '5', '10', '15', '30', '60', '75', '120', '125', '180', '240'],
          daily_multipliers: ['1'],
          weekly_multipliers: ['1'],
          monthly_multipliers: ['1'],
          volume_precision: 0,
          data_status: 'streaming',
          currency_code: 'INR',
        };

        // Verify symbol exists via quote API
        kiteFetch(`/quote?i=${exchange}:${encodeURIComponent(cleanSymbol)}`)
          .then((res) => {
            if (!res.ok) {
              // Still resolve — the symbol might work with historical API
              onResolve(symbolInfo);
              return;
            }
            return res.json().then(() => {
              onResolve(symbolInfo);
            });
          })
          .catch(() => {
            // Resolve anyway to avoid blocking the widget
            onResolve(symbolInfo);
          });
      }, 0);
    },

    async getBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: ResolutionString,
      periodParams: PeriodParams,
      onResult: HistoryCallback,
      onError: ErrorCallback,
    ): Promise<void> {
      const symbol = symbolInfo.name;
      const exchange = symbolInfo.exchange || 'NSE';
      const kiteInterval = RESOLUTION_TO_KITE_INTERVAL[resolution] ?? 'minute';
      const timeframe = RESOLUTION_TO_TIMEFRAME[resolution] ?? '1m';

      // The exact [from, to] window TradingView is asking for. TV walks this
      // window backward one page at a time as the user scrolls the chart left.
      const from = new Date(periodParams.from * 1000);
      const to = new Date(periodParams.to * 1000);
      const fromMs = from.getTime();
      const toMs = to.getTime();

      try {
        // 1. Pull any bars we already fetched for this symbol/timeframe that
        //    overlap the requested window. If the cache already fully covers
        //    the window, we don't hit the network at all — TV re-renders from
        //    memory and scroll-back is instant.
        const cached = readScrollBackCache(symbol, timeframe, fromMs, toMs);

        // 2. Find the oldest cached bar inside the requested window. If we have
        //    a continuous run covering [oldestCachedAt, to], we only need to
        //    fetch the missing slice [from, oldestCachedAt). Otherwise fetch
        //    the full window.
        let fetchFrom = from;
        if (cached.length > 0) {
          const oldestCached = cached[0].time;
          if (oldestCached <= fromMs) {
            // Cache fully covers the window — no network roundtrip.
            onResult(cached, { noData: false });
            return;
          }
          fetchFrom = new Date(oldestCached - 1);
          // Re-filter cached bars to the actual gap we're filling.
        }

        // 3. Fetch the missing slice (or the whole window on a cold cache).
        let fetched = await fetchKiteBatch(
          symbol,
          kiteInterval,
          fetchFrom,
          new Date(toMs),
          exchange,
          timeframe,
        );

        // 4. Merge the freshly fetched bars into the persistent cache so the
        //    next scroll-back call can serve them from memory.
        if (fetched.length > 0) {
          mergeScrollBackCache(symbol, timeframe, fetched);
        }

        // 5. Read the FULL [from, to] window back out of the merged cache so
        //    TV gets a continuous bar set spanning exactly what it asked for,
        //    regardless of where the freshly-fetched slice landed.
        let bars = readScrollBackCache(symbol, timeframe, fromMs, toMs);

        // ── FNO-specific: detect if the symbol is an F&O contract ──────
        const symbolIsFno = isFnoSymbol(symbol);

        if (bars.length === 0) {
          const earliest = earliestKnownBar(symbol, timeframe);

          // For FNO symbols, the Tauri/QuestDB cache is the SOLE data source.
          // If the requested window is entirely before the earliest bar, there
          // is genuinely no older data — stop immediately with a nextTime hint.
          if (symbolIsFno && earliest !== undefined && toMs < earliest) {
            onResult([], { noData: true, nextTime: Math.floor(earliest / 1000) });
            return;
          }

          // For all symbols: double-check pattern. First empty hit returns
          // noData: false so TV retries. Second empty hit for the same window
          // returns noData: true to stop pagination.
          const exKey = exhaustionKey(symbol, timeframe, periodParams.from);
          if (exhaustedWindows.has(exKey)) {
            const meta: { noData: boolean; nextTime?: number } = { noData: true };
            if (earliest !== undefined) {
              meta.nextTime = Math.floor(earliest / 1000);
            }
            onResult([], meta);
          } else {
            exhaustedWindows.add(exKey);
            onResult([], { noData: false });
          }
          return;
        }

        // For FNO symbols only: if the fetch returned zero NEW bars and we're
        // past the cache edge, mark exhaustion so TV stops on the next page.
        // Don't do this for equity — REST scroll-back would be blocked.
        if (symbolIsFno && fetched.length === 0 && cached.length > 0) {
          const earliest = earliestKnownBar(symbol, timeframe);
          if (earliest !== undefined && fromMs < earliest) {
            const exKey = exhaustionKey(symbol, timeframe, periodParams.from);
            exhaustedWindows.add(exKey);
          }
        }

        // Sort ascending by time (TV requirement) — readScrollBackCache already
        // sorts, but be defensive in case of a future change.
        bars.sort((a, b) => a.time - b.time);

        // Deduplicate by time (safety net; the cache already dedups by key).
        const seen = new Set<number>();
        bars = bars.filter((b) => {
          if (seen.has(b.time)) return false;
          seen.add(b.time);
          return true;
        });

        // ── Mirror bars into the Zustand historicalCache ──────────────
        // The Deep Quant / Consensus pipeline gates on
        // `useTradeStore.historicalCache` (via symbolCandleCount) to know a
        // symbol has data. Each scroll-back page is MERGED with the existing
        // cache so pages accumulate rather than overwrite.
        try {
          const cacheKey = `${symbol.toUpperCase()}::${timeframe}::${kiteInterval}`;
          const store = useTradeStore.getState();
          const existing = store.historicalCache[cacheKey] ?? [];
          const mergedByTime = new Map<number, OhlcCandle>();
          for (const c of existing) mergedByTime.set(c.start_timestamp_ms, c);
          for (const b of bars) {
            mergedByTime.set(b.time, {
              symbol: symbol.toUpperCase(),
              start_timestamp_ms: b.time, // datafeed bars are in ms
              open: b.open,
              high: b.high,
              low: b.low,
              close: b.close,
              volume: b.volume ?? 0,
            });
          }
          const merged = Array.from(mergedByTime.values()).sort(
            (a, b) => a.start_timestamp_ms - b.start_timestamp_ms,
          );
          store.setHistoricalCache(cacheKey, merged);
        } catch (cacheErr) {
          console.warn('[Datafeed] historicalCache mirror failed:', cacheErr);
        }

        onResult(bars, { noData: false });
      } catch (err) {
        console.error('[Datafeed] getBars failed:', err);
        onError(String(err));
      }
    },

    subscribeBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: ResolutionString,
      onTick: SubscribeBarsCallback,
      listenerGuid: string,
      _onResetCacheNeededCallback: () => void,
    ): void {
      startLiveSubscription(symbolInfo.name, resolution, onTick, listenerGuid);
    },

    unsubscribeBars(listenerGuid: string): void {
      const sub = activeSubscriptions.get(listenerGuid);
      if (sub) {
        sub.unsubscribe();
        activeSubscriptions.delete(listenerGuid);
      }
    },
  };
}
