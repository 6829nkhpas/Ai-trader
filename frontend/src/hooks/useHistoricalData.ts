// hooks/useHistoricalData.ts — Fetch historical OHLCV from QuestDB REST API
//
// Works in BOTH browser mode (localhost:3000) and Tauri mode.
// QuestDB exposes a REST API on port 9000 that accepts SQL queries.
//
// Endpoint: GET http://localhost:9000/exec?query=SELECT...&fmt=json

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

// URL for QuestDB REST API.
// In browser/dev mode: use the Next.js proxy rewrite (/questdb/* → :9000)
// In Tauri (packaged app): use the direct address — no Next.js server exists.
const QUESTDB_BROWSER_URL = '/questdb/exec';
const QUESTDB_DIRECT_URL = 'http://127.0.0.1:9000/exec';

const getQuestDbUrl = () => isTauri() ? QUESTDB_DIRECT_URL : QUESTDB_BROWSER_URL;

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
 * Fetch historical data from QuestDB via REST API.
 * Used as both the primary browser path and a fallback for Tauri when IPC fails.
 */
async function fetchFromQuestDB(symbol: string): Promise<HistoricalCandle[]> {
  const QUESTDB_URL = getQuestDbUrl();

  // Try historical_candles first; fall back to live_ticks if it doesn't exist.
  const tables = [
    `SELECT ts, open, high, low, close, volume FROM historical_candles WHERE symbol = '${symbol}' ORDER BY ts ASC`,
    `SELECT timestamp as ts, last_price as open, last_price as high, last_price as low, last_price as close, volume FROM live_ticks WHERE symbol = '${symbol}' ORDER BY timestamp ASC LIMIT 1000`,
  ];

  for (const query of tables) {
    try {
      const url = `${QUESTDB_URL}?query=${encodeURIComponent(query)}&fmt=json`;
      const response = await fetch(url);
      if (!response.ok) continue;
      const data: QuestDBResponse = await response.json();
      if (data.error || !data.dataset || data.dataset.length === 0) continue;

      const parsed: HistoricalCandle[] = data.dataset.map((row) => {
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
      }).filter((c) => c.time > 0 && c.open > 0);

      console.log(`[Historical] ${symbol}: ${parsed.length} candles loaded from QuestDB (${isTauri() ? 'Tauri direct' : 'browser proxy'})`);
      return parsed;
    } catch (err) {
      console.warn('[Historical] Query attempt failed:', err);
    }
  }

  console.warn(`[Historical] All QuestDB queries failed for ${symbol} — no historical data available.`);
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
        // --- TAURI IPC PATH (Binary Serialization) ---
        // Try IPC first; fall back to REST API if the command isn't available
        // (e.g. QuestDB is empty, or `get_historical_view` isn't registered yet).
        try {
          // Dynamic import prevents breaking web-only builds where
          // @tauri-apps/api/core may not be installed.
          const tauri = await import('@tauri-apps/api/core');
          const response = await tauri.invoke<number[] | Uint8Array>('get_historical_view', { symbol });
          const binaryBuffer = response instanceof Uint8Array ? response : new Uint8Array(response);
          const parsed = parseBincodeCandles(binaryBuffer);

          console.log(
            `[Historical Tauri IPC] ${symbol}: ${parsed.length} candles loaded via zero-latency buffer`
          );
          setCandles(parsed);
          return; // IPC succeeded — no REST fallback needed
        } catch (ipcErr) {
          // IPC failed (command not registered, QuestDB empty, etc.)
          // Fall through to REST API.
          console.warn(
            `[Historical] Tauri IPC 'get_historical_view' failed for ${symbol}:`,
            ipcErr,
            '→ falling back to QuestDB REST API'
          );
        }
      }

      // --- WEB BROWSER PATH (or Tauri IPC fallback) ---
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
