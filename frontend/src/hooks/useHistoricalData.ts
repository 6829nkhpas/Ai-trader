// hooks/useHistoricalData.ts — Fetch historical OHLCV from QuestDB REST API
//
// Works in BOTH browser mode (localhost:3000) and Tauri mode.
// QuestDB exposes a REST API on port 9000 that accepts SQL queries.
//
// Endpoint: GET http://localhost:9000/exec?query=SELECT...&fmt=json
//
// ── Tauri-Specific Fixes ──────────────────────────────────────────────────
//   1. Pool Race Condition: The QuestDB PgPool is registered asynchronously
//      in lib.rs. We now poll `get_pool_status` with retries before calling
//      `get_historical_view` to avoid "state not managed" errors.
//   2. CORS Bypass: In production Tauri builds (tauri:// origin), direct
//      fetch() to http://127.0.0.1:9000 fails due to CORS. The fallback
//      now uses the `fetch_questdb` IPC command that proxies through Rust.

import { useState, useEffect, useCallback } from 'react';
import { useTradeStore, type OhlcCandle } from '../store/useTradeStore';
import { kiteFetch } from '../lib/kiteFetch';
import { debugLog } from '../lib/debugLog';

export interface HistoricalCandle {
  /** Seconds since Unix epoch (lightweight-charts format) */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface QuestDBResponse {
  query: string;
  columns: { name: string; type: string }[];
  dataset: (string | number | null)[][] | null;
  count: number;
  error?: string;
}

interface UseHistoricalDataReturn {
  candles: HistoricalCandle[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}


// URL for QuestDB REST API (browser-only path via Next.js proxy).
const QUESTDB_BROWSER_URL = '/questdb/exec';

/**
 * Parse QuestDB JSON response rows into HistoricalCandle[].
 */
function parseQuestDBRows(dataset: (string | number | null)[][]): HistoricalCandle[] {
  return dataset
    .map((row) => {
      const tsStr = row[0] as string;
      const timeSec = Math.floor(new Date(tsStr).getTime() / 1000);
      return {
        time: timeSec,
        open: Number(row[1]),
        high: Number(row[2]),
        low: Number(row[3]),
        close: Number(row[4]),
        volume: Number(row[5]),
      };
    })
    .filter((c) => c.time > 0 && c.open > 0);
}

// ── SQL queries to try in order ─────────────────────────────────────────────
function getQueries(symbol: string): string[] {
  return [
    `SELECT ts, open, high, low, close, volume FROM historical_candles WHERE symbol = '${symbol}' ORDER BY ts ASC`,
    `SELECT timestamp as ts, last_price as open, last_price as high, last_price as low, last_price as close, volume FROM live_ticks WHERE symbol = '${symbol}' ORDER BY timestamp ASC LIMIT 1000`,
  ];
}

/**
 * Fetch historical data from QuestDB through the same-origin `/questdb/*` route
 * handler, which holds the gateway credential server-side.
 */
async function fetchFromQuestDB(symbol: string): Promise<HistoricalCandle[]> {
  const queries = getQueries(symbol);

  for (const query of queries) {
    try {
      const url = `${QUESTDB_BROWSER_URL}?query=${encodeURIComponent(query)}&fmt=json`;
      const response = await fetch(url);
      if (!response.ok) continue;
      const data: QuestDBResponse = await response.json();
      if (data.error || !data.dataset || data.dataset.length === 0) continue;

      const parsed = parseQuestDBRows(data.dataset);
      debugLog(
        `[Historical] ${symbol}: ${parsed.length} candles loaded from QuestDB (browser proxy)`
      );
      return parsed;
    } catch (err) {
      console.warn('[Historical] Query attempt failed:', err);
    }
  }

  console.warn(
    `[Historical] All QuestDB queries failed for ${symbol} — no historical data available.`
  );
  return [];
}

/**
 * Resolve a symbol's Kite instrument_token via the quote API.
 * The quote endpoint (`/kite/quote?i=NSE:SYMBOL`) works even when the
 * server-side instrument CSV cache is empty, making it a reliable fallback
 * for token resolution.
 */
async function resolveInstrumentToken(symbol: string): Promise<number | null> {
  try {
    const sym = symbol.toUpperCase();
    const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
    const exchange = isFno ? 'NFO' : 'NSE';
    const res = await kiteFetch(`/quote?i=${exchange}:${encodeURIComponent(symbol)}`);
    if (!res.ok) return null;
    const data = await res.json();
    const quotes = data.quotes as { symbol: string; instrument_token: number }[] | undefined;
    // Exact tradingsymbol only. This used to fall through to `quotes[0]`, which
    // is how a request for the BSE index SENSEX came back holding an NSE ETF that
    // tracks it and charted that instead — see `datafeed.ts::resolveInstrumentToken`.
    const wanted = symbol.trim().toUpperCase();
    return quotes?.find((q) => q.symbol?.toUpperCase() === wanted)?.instrument_token ?? null;
  } catch {
    return null;
  }
}

// Cache resolved tokens to avoid repeated quote API calls during parallel fetches
const tokenCache = new Map<string, number>();

/**
 * Fetch a single batch of historical candles from the Kite Historical API via
 * the aggregator's REST proxy at /kite/historical.
 *
 * MUST go through `kiteFetch`, never a bare `fetch('/kite/...')`. A relative
 * path only resolves under `npm run dev`, where the Next.js rewrite proxies it;
 * the packaged app is a static export with no rewrites, so a bare fetch hits
 * `tauri.localhost` and 404s. `kiteFetch` routes through the Rust `kite_fetch`
 * command (gateway base + credentials injected server-side) under Tauri and
 * falls back to the dev rewrite in a plain browser. Pass the path AFTER `/kite`.
 *
 * When the symbol-based request fails (server can't resolve the symbol because
 * the instrument CSV cache is empty), we resolve the instrument_token via the
 * quote API and retry with the token directly.
 */
async function fetchKiteBatch(
  symbol: string,
  interval: string,
  daysBack: number
): Promise<HistoricalCandle[]> {
  const parseCandles = (data: any): HistoricalCandle[] =>
    (data.candles || [])
      .map((c: { time: number; open: number; high: number; low: number; close: number; volume: number }) => ({
        time: c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      }))
      .filter((c: HistoricalCandle) => c.time > 0 && c.open > 0);

  const to = new Date();
  const from = new Date(to.getTime() - daysBack * 24 * 60 * 60 * 1000);
  const fmt = (d: Date) => d.toISOString().slice(0, 10); // yyyy-mm-dd
  const dateParams = `&from=${fmt(from)}&to=${fmt(to)}`;

  try {
    // Attempt 1: Use symbol name (works if server instrument cache is populated)
    const url = `/historical?symbol=${encodeURIComponent(symbol)}&interval=${interval}${dateParams}`;
    const response = await kiteFetch(url);
    if (response.ok) {
      const data = await response.json();
      const candles = parseCandles(data);
      if (candles.length > 0) return candles;
    }

    // Attempt 2: Resolve instrument_token via quote API and retry with token
    let token = tokenCache.get(symbol.toUpperCase());
    if (!token) {
      const resolved = await resolveInstrumentToken(symbol);
      if (!resolved) {
        console.warn(`[Historical] Could not resolve instrument token for ${symbol}`);
        return [];
      }
      token = resolved;
      tokenCache.set(symbol.toUpperCase(), token);
    }

    const tokenUrl = `/historical?instrument_token=${token}&interval=${interval}${dateParams}`;
    const tokenResponse = await kiteFetch(tokenUrl);
    if (!tokenResponse.ok) return [];
    const tokenData = await tokenResponse.json();
    return parseCandles(tokenData);
  } catch {
    return [];
  }
}

/**
 * Fetch historical candles directly from the Kite Historical API via the
 * aggregator's REST proxy at /kite/historical.
 *
 * This is the fallback when QuestDB has no data for a symbol (i.e. the symbol
 * was never ingested via the Tauri history_loader).
 *
 * **Timeframe-aware:** For intraday timeframes we ONLY fetch intraday candles;
 * for daily+ timeframes we ONLY fetch daily candles.  Mixing the two caused
 * massive price-axis distortion (old daily prices plotted next to current
 * intraday prices).
 *
 * @param symbol      — NSE trading symbol (e.g. "INFY").
 * @param rangeDays   — How many days of data to fetch (e.g. 365, 1825).
 * @param kiteInterval — The Kite API interval string (e.g. 'minute', '10minute', 'day').
 */
async function fetchFromKiteHistorical(
  symbol: string,
  rangeDays: number = 365,
  kiteInterval: string = '10minute',
): Promise<HistoricalCandle[]> {
  try {
    const isDailyOrAbove = kiteInterval === 'day';

    if (isDailyOrAbove) {
      // Daily+ timeframe: only daily candles, full range
      const candles = await fetchKiteBatch(symbol, 'day', rangeDays);
      if (candles.length > 0) {
        debugLog(
          `[Historical] ${symbol}: ${candles.length} daily candles loaded (range=${rangeDays}d)`
        );
      }
      return candles;
    }

    // Intraday timeframe: Kite limits intraday to ~60 days
    const intradayDays = Math.min(rangeDays, 60);
    const candles = await fetchKiteBatch(symbol, kiteInterval, intradayDays);
    if (candles.length > 0) {
      debugLog(
        `[Historical] ${symbol}: ${candles.length} ${kiteInterval} candles loaded (range=${intradayDays}d)`
      );
    }
    return candles;
  } catch (err) {
    console.warn('[Historical] Kite historical API fetch failed:', err);
    return [];
  }
}

/**
 * React hook to fetch historical OHLCV data from QuestDB's REST API.
 *
 * @param symbol            — Instrument symbol (e.g., "RELIANCE"). Empty string skips fetch.
 * @param rangeDays         — How many days of historical data to request (default 365).
 * @param kiteInterval      — Kite API interval string for the active timeframe (default '10minute').
 * @param effectiveTimeframe — UI timeframe string (e.g. '1m', '2m'). Included in fetchData deps
 *                            so that switching between timeframes with the same kiteInterval
 *                            (e.g. 1m→2m, both 'minute') still triggers a cache re-evaluation
 *                            and causes historicalCandles to get a new array reference, which
 *                            in turn allows aggregateCandles() to re-run with the new timeframe.
 */
export function useHistoricalData(
  symbol: string,
  rangeDays: number = 365,
  kiteInterval: string = '10minute',
  effectiveTimeframe: string = '10m',
): UseHistoricalDataReturn {
  const [candles, setCandles] = useState<HistoricalCandle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!symbol) return;

    // ── DIAGNOSTIC TRACER — UI → RUST historical fetch dispatch ──
    // The Rust `get_historical_view` IPC now accepts a `timeframe` arg and
    // dynamically aggregates QuestDB ticks via SAMPLE BY. We pass the live
    // UI timeframe verbatim so the backend returns bars at the requested
    // resolution (no client-side resampling needed for the primary path).
    debugLog(
      "[UI DISPATCH] Fetching History - Symbol:", symbol,
      "Timeframe:", effectiveTimeframe,
      "(kiteInterval:", kiteInterval, ", rangeDays:", rangeDays, ")"
    );


    // ── Two-tier cache ──────────────────────────────────────────────────
    // L1 (QuestDB, persistent): Backend stores Kite data at the BASE interval
    //     (e.g., "minute" for 1m/2m/4m). Derived timeframes share the same L1
    //     cache → no redundant Kite API calls.
    // L2 (here, session): Each UI timeframe gets its own cache slot so
    //     re-visiting a timeframe is instant (no backend IPC roundtrip).
    //     aggregateCandles() re-buckets the base-interval data into the exact
    //     UI timeframe on each cache hit.
    const cacheKey = `${symbol.toUpperCase()}::${effectiveTimeframe}::${kiteInterval}`;
    const cached = useTradeStore.getState().historicalCache[cacheKey];
    if (cached && cached.length > 0) {
      const asHistorical: HistoricalCandle[] = cached.map((c) => ({
        time: Math.floor(c.start_timestamp_ms / 1000),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      }));
      debugLog(`[Historical] ${symbol}: ${asHistorical.length} candles from cache (tf=${effectiveTimeframe}, interval=${kiteInterval})`);
      setCandles(asHistorical);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    // ── Hard data wipe before fetching ─────────────────────────────────
    // The merged-candle wipe at the store level (clearLiveBuffer / activeTf
    // flush) handles live ticks. Here we wipe the hook's local historical
    // state so the parent's useMemo recomputes mergedCandles=[] →
    // chartData=[] → useChartDataSync calls setData([]) on every series.
    // This prevents 1m live candles from being stitched onto a fresh 1H
    // historical pull during the IPC roundtrip.
    setCandles([]);

    try {
      // QuestDB first (the ingested cache), then Kite for anything that was
      // never ingested. `/questdb/*` is rewritten to the same-origin route
      // handler, which is what keeps the gateway credential server-side.
      let parsed = await fetchFromQuestDB(symbol);

      // If QuestDB has no data for this symbol, fall back to the Kite
      // Historical API which can serve candles for any NSE instrument.
      if (parsed.length === 0) {
        parsed = await fetchFromKiteHistorical(symbol, rangeDays, kiteInterval);
      }

      // Populate cache for instant re-visits
      if (parsed.length > 0) {
        const asOhlc: OhlcCandle[] = parsed.map((c) => ({
          symbol: symbol.toUpperCase(),
          start_timestamp_ms: c.time * 1000, // seconds → ms
          open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume,
        }));
        useTradeStore.getState().setHistoricalCache(cacheKey, asOhlc);
      }

      setCandles(parsed);
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : e?.message || 'Unknown error';
      console.error(`[Historical] Failed to fetch ${symbol}:`, msg);
      setError(msg);

      // BUG-8: Cross-interval fallback with direction guard.
      // Can aggregate finer data UP (1m→5m) but CANNOT split coarser DOWN (10m→1m).
      // Cache keys are now `${SYMBOL}::${TF}::${INTERVAL}` — split on '::'
      // and read [2] for the Kite interval portion.
      const INTERVAL_MINUTES: Record<string, number> = {
        'minute': 1, '3minute': 3, '5minute': 5, '10minute': 10,
        '15minute': 15, '30minute': 30, '60minute': 60, 'day': 1440,
      };
      const requestedMinutes = INTERVAL_MINUTES[kiteInterval] ?? 10;
      const allCache = useTradeStore.getState().historicalCache;
      const symPrefix = `${symbol.toUpperCase()}::`;
      const fallbackEntry = Object.entries(allCache).find(([key, val]) => {
        if (!key.startsWith(symPrefix) || !val || val.length === 0) return false;
        const parts = key.split('::');
        const fallbackInterval = parts[2] ?? parts[1] ?? '';
        const fallbackMinutes = INTERVAL_MINUTES[fallbackInterval] ?? 99999;
        // Only use fallback whose resolution is FINER (smaller) than requested.
        return fallbackMinutes <= requestedMinutes;
      });
      if (fallbackEntry) {
        const [fallbackKey, fallbackData] = fallbackEntry;
        console.warn(
          `[Historical] ${symbol}: cross-interval fallback — '${fallbackKey}' ` +
          `(${fallbackData.length} candles) for tf=${effectiveTimeframe}`
        );
        const asHistorical: HistoricalCandle[] = fallbackData.map((c) => ({
          time: Math.floor(c.start_timestamp_ms / 1000),
          open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume,
        }));
        setCandles(asHistorical);
      }
    } finally {
      setLoading(false);
    }
  // effectiveTimeframe in deps drives a fresh fetchData on tf switch — the
  // backend's SAMPLE BY pipeline now returns the correct aggregation directly.
  }, [symbol, rangeDays, kiteInterval, effectiveTimeframe]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { candles, loading, error, refetch: fetchData };
}
