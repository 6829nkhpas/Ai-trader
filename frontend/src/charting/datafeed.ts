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
import { useTradeStore } from '../store/useTradeStore';

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

/** Convert TV resolution to a UI timeframe string for the Tauri IPC. */
const RESOLUTION_TO_TIMEFRAME: Record<string, string> = {
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

/** How many days of intraday data Kite serves (hard limit). */
const KITE_INTRADAY_MAX_DAYS = 60;

// ── Tauri Detection ───────────────────────────────────────────────────────
const isTauri = () =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

// ── Instrument Token Cache ────────────────────────────────────────────────
const tokenCache = new Map<string, number>();

async function resolveInstrumentToken(symbol: string): Promise<number | null> {
  const cached = tokenCache.get(symbol.toUpperCase());
  if (cached) return cached;
  try {
    const res = await fetch(`/kite/quote?i=NSE:${encodeURIComponent(symbol)}`);
    if (!res.ok) return null;
    const data = await res.json();
    const quotes = data.quotes as { symbol: string; instrument_token: number }[] | undefined;
    if (!quotes || quotes.length === 0) return null;
    const match = quotes.find((q) => q.symbol.toUpperCase() === symbol.toUpperCase());
    const token = match?.instrument_token ?? quotes[0].instrument_token ?? null;
    if (token) tokenCache.set(symbol.toUpperCase(), token);
    return token;
  } catch {
    return null;
  }
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
): Promise<Bar[]> {
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  const dateParams = `&from=${fmt(from)}&to=${fmt(to)}`;

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

  try {
    // Attempt 1: symbol name
    const url = `/kite/historical?symbol=${encodeURIComponent(symbol)}&interval=${interval}${dateParams}`;
    const response = await fetch(url);
    if (response.ok) {
      const data = await response.json();
      const candles = parseCandles(data);
      if (candles.length > 0) return candles;
    }

    // Attempt 2: instrument_token
    const token = await resolveInstrumentToken(symbol);
    if (!token) return [];
    const tokenUrl = `/kite/historical?instrument_token=${token}&interval=${interval}${dateParams}`;
    const tokenResponse = await fetch(tokenUrl);
    if (!tokenResponse.ok) return [];
    const tokenData = await tokenResponse.json();
    return parseCandles(tokenData);
  } catch {
    return [];
  }
}

// ── Tauri IPC Historical Fetch ────────────────────────────────────────────

async function fetchTauriBars(
  symbol: string,
  timeframe: string,
): Promise<Bar[]> {
  if (!isTauri()) return [];
  try {
    const tauri = await import('@tauri-apps/api/core');

    // Wait for QuestDB pool
    let poolReady = false;
    const start = Date.now();
    while (Date.now() - start < 8000) {
      try {
        poolReady = await tauri.invoke<boolean>('get_pool_status');
        if (poolReady) break;
      } catch { /* pool not ready yet */ }
      await new Promise((r) => setTimeout(r, 500));
    }

    if (!poolReady) return [];

    const response = await tauri.invoke<number[] | Uint8Array>(
      'get_historical_view',
      { symbol, timeframe },
    );
    const buffer =
      response instanceof Uint8Array ? response : new Uint8Array(response);

    return parseBincodeCandles(buffer);
  } catch (err) {
    console.warn('[Datafeed] Tauri IPC fetch failed:', err);
    return [];
  }
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

  const unsub = useTradeStore.subscribe((state) => {
    const candles = state.ohlcCandles;
    const matching = candles.filter(
      (c) => c.symbol.toUpperCase() === symbolUpper,
    );
    if (matching.length === 0) return;

    const latest = matching[matching.length - 1];
    const barTimeMs = latest.start_timestamp_ms;

    // Only forward if this is a new or updated bar
    if (barTimeMs >= lastBarTime) {
      lastBarTime = barTimeMs;
      onTick({
        time: barTimeMs,
        open: latest.open,
        high: latest.high,
        low: latest.low,
        close: latest.close,
        volume: latest.volume ?? 0,
      });
    }
  });

  activeSubscriptions.set(listenerGuid, {
    symbol, resolution, onTick, unsubscribe: unsub,
  });
}

// ── Datafeed Implementation ───────────────────────────────────────────────

export function createDatafeed(): IBasicDatafeed {
  return {
    onReady(callback: OnReadyCallback): void {
      // TV requires async callback
      setTimeout(() => {
        const config: DatafeedConfiguration = {
          exchanges: [
            { value: 'NSE', name: 'NSE', desc: 'National Stock Exchange' },
            { value: 'BSE', name: 'BSE', desc: 'Bombay Stock Exchange' },
          ],
          symbols_types: [
            { name: 'Stock', value: 'stock' },
            { name: 'Index', value: 'index' },
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

      fetch(`/kite/quote?i=NSE:${encodeURIComponent(userInput)}`)
        .then((res) => (res.ok ? res.json() : { quotes: [] }))
        .then((data) => {
          const quotes = (data.quotes || []) as {
            symbol: string;
            instrument_token: number;
          }[];
          onResult(
            quotes.map((q) => ({
              symbol: q.symbol,
              full_name: `NSE:${q.symbol}`,
              description: q.symbol,
              exchange: 'NSE',
              ticker: `NSE:${q.symbol}`,
              type: 'stock',
            })),
          );
        })
        .catch(() => onResult([]));
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

      setTimeout(() => {
        const symbolInfo: LibrarySymbolInfo = {
          name: cleanSymbol,
          full_name: `NSE:${cleanSymbol}`,
          ticker: `NSE:${cleanSymbol}`,
          description: cleanSymbol,
          type: 'stock',
          session: '0915-1530',
          timezone: 'Asia/Kolkata',
          exchange: 'NSE',
          listed_exchange: 'NSE',
          format: 'price',
          minmov: 1,
          pricescale: 100, // 2 decimal places (₹XX.XX)
          has_intraday: true,
          has_daily: true,
          has_weekly_and_monthly: true,
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          volume_precision: 0,
          data_status: 'streaming',
          currency_code: 'INR',
        };

        // Verify symbol exists via quote API
        fetch(`/kite/quote?i=NSE:${encodeURIComponent(cleanSymbol)}`)
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
      const kiteInterval = RESOLUTION_TO_KITE_INTERVAL[resolution] ?? 'minute';
      const timeframe = RESOLUTION_TO_TIMEFRAME[resolution] ?? '1m';
      const isDailyOrAbove = kiteInterval === 'day';

      const from = new Date(periodParams.from * 1000);
      const to = new Date(periodParams.to * 1000);

      try {
        let bars: Bar[] = [];

        // ── Tauri IPC path (fastest) ──────────────────────────────────
        if (isTauri()) {
          bars = await fetchTauriBars(symbol, timeframe);
          if (bars.length > 0) {
            // Filter to requested range
            const fromMs = periodParams.from * 1000;
            const toMs = periodParams.to * 1000;
            bars = bars.filter((b) => b.time >= fromMs && b.time <= toMs);
          }
        }

        // ── Kite Historical API (browser / fallback) ──────────────────
        if (bars.length === 0) {
          if (!isDailyOrAbove) {
            // Kite limits intraday to ~60 days
            const daysDiff = Math.ceil(
              (to.getTime() - from.getTime()) / (24 * 60 * 60 * 1000),
            );
            const clampedFrom = daysDiff > KITE_INTRADAY_MAX_DAYS
              ? new Date(to.getTime() - KITE_INTRADAY_MAX_DAYS * 24 * 60 * 60 * 1000)
              : from;
            bars = await fetchKiteBatch(symbol, kiteInterval, clampedFrom, to);
          } else {
            bars = await fetchKiteBatch(symbol, kiteInterval, from, to);
          }
        }

        if (bars.length === 0) {
          onResult([], { noData: true });
          return;
        }

        // Sort ascending by time (TV requirement)
        bars.sort((a, b) => a.time - b.time);

        // Deduplicate by time
        const seen = new Set<number>();
        bars = bars.filter((b) => {
          if (seen.has(b.time)) return false;
          seen.add(b.time);
          return true;
        });

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
