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

// Check if running in Tauri environment
const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

// URL for QuestDB REST API (browser-only path via Next.js proxy).
const QUESTDB_BROWSER_URL = '/questdb/exec';

/**
 * Parses a bincode-serialized byte array of `BinaryCandle` structs into an array of `HistoricalCandle`.
 * Each `BinaryCandle` in Rust is: ts (i64), open (f64), high (f64), low (f64), close (f64), volume (i64) = 48 bytes.
 * Note: bincode serialization of a Vec<T> includes an 8-byte length prefix (u64).
 */
function parseBincodeCandles(buffer: Uint8Array): HistoricalCandle[] {
  const candles: HistoricalCandle[] = [];
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);

  // Read the 8-byte length prefix (number of items)
  const length = Number(view.getBigUint64(0, true));

  let offset = 8;
  for (let i = 0; i < length; i++) {
    // bincode serializes in little-endian by default
    const tsMicro = Number(view.getBigInt64(offset, true));
    const open = view.getFloat64(offset + 8, true);
    const high = view.getFloat64(offset + 16, true);
    const low = view.getFloat64(offset + 24, true);
    const close = view.getFloat64(offset + 32, true);
    const volume = Number(view.getBigInt64(offset + 40, true));

    // Convert microseconds to seconds for lightweight-charts
    const timeSec = Math.floor(tsMicro / 1000000);

    candles.push({
      time: timeSec,
      open,
      high,
      low,
      close,
      volume,
    });

    offset += 48; // Advance by the size of one BinaryCandle struct
  }

  return candles;
}

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
 * Wait for QuestDB PgPool to be registered as Tauri managed state.
 * Polls `get_pool_status` every 500ms for up to `maxWaitMs`.
 */
async function waitForPool(tauri: any, maxWaitMs = 8000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    try {
      const ready: boolean = await tauri.invoke('get_pool_status');
      if (ready) return true;
    } catch {
      // Command itself might fail if Tauri is still initializing
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

/**
 * Fetch historical data from QuestDB via the `fetch_questdb` IPC command.
 * This proxies the HTTP request through Rust, completely bypassing CORS.
 * Used as the Tauri fallback when the primary bincode IPC path fails.
 */
async function fetchViaIpcProxy(
  tauri: any,
  symbol: string
): Promise<HistoricalCandle[]> {
  const queries = getQueries(symbol);

  for (const query of queries) {
    try {
      const rawJson: string = await tauri.invoke('fetch_questdb', { query });
      const data: QuestDBResponse = JSON.parse(rawJson);
      if (data.error || !data.dataset || data.dataset.length === 0) continue;

      const parsed = parseQuestDBRows(data.dataset);
      console.log(
        `[Historical] ${symbol}: ${parsed.length} candles loaded via Tauri IPC proxy (fetch_questdb)`
      );
      return parsed;
    } catch (err) {
      console.warn('[Historical] IPC proxy query attempt failed:', err);
    }
  }

  console.warn(
    `[Historical] All IPC proxy queries failed for ${symbol} — no historical data available.`
  );
  return [];
}

/**
 * Fetch historical data from QuestDB via browser fetch() + Next.js proxy.
 * Only used in non-Tauri (browser) mode where /questdb/* proxy is available.
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
      console.log(
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
 * React hook to fetch historical OHLCV data from QuestDB's REST API.
 *
 * @param symbol — Instrument symbol (e.g., "RELIANCE"). Empty string skips fetch.
 */
export function useHistoricalData(symbol: string): UseHistoricalDataReturn {
  const [candles, setCandles] = useState<HistoricalCandle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!symbol) return;

    setLoading(true);
    setError(null);

    try {
      if (isTauri()) {
        // ── TAURI PATH ──────────────────────────────────────────────────
        // Dynamic import prevents breaking web-only builds where
        // @tauri-apps/api/core may not be installed.
        const tauri = await import('@tauri-apps/api/core');

        // Step 1: Wait for the QuestDB PgPool to be registered as managed
        // state. The pool is initialized asynchronously in lib.rs — calling
        // get_historical_view before it's ready causes "state not managed".
        const poolReady = await waitForPool(tauri);

        if (poolReady) {
          // Step 2a: Try the primary bincode IPC path (zero-latency)
          try {
            const response = await tauri.invoke<number[] | Uint8Array>(
              'get_historical_view',
              { symbol }
            );
            const binaryBuffer =
              response instanceof Uint8Array ? response : new Uint8Array(response);
            const parsed = parseBincodeCandles(binaryBuffer);

            console.log(
              `[Historical Tauri IPC] ${symbol}: ${parsed.length} candles loaded via zero-latency buffer`
            );
            if (parsed.length > 0) {
              setCandles(parsed);
              return; // Success — done
            }
            // IPC returned 0 candles — fall through to HTTP proxy
          } catch (ipcErr) {
            console.warn(
              `[Historical] Tauri IPC 'get_historical_view' failed for ${symbol}:`,
              ipcErr,
              '→ falling back to IPC proxy'
            );
          }
        } else {
          console.warn(
            `[Historical] QuestDB pool not ready after timeout — skipping bincode path for ${symbol}`
          );
        }

        // Step 2b: Fallback — use fetch_questdb IPC command which proxies
        // the HTTP request through Rust, bypassing CORS entirely.
        // This works even when the PgPool isn't ready (uses HTTP, not PG).
        const parsed = await fetchViaIpcProxy(tauri, symbol);
        setCandles(parsed);
        return;
      }

      // ── BROWSER PATH ────────────────────────────────────────────────
      // Uses the Next.js proxy rewrite: /questdb/* → localhost:9000
      const parsed = await fetchFromQuestDB(symbol);
      setCandles(parsed);
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : e?.message || 'Unknown error';
      console.error(`[Historical] Failed to fetch ${symbol}:`, msg);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { candles, loading, error, refetch: fetchData };
}
