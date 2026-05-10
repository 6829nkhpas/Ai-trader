// hooks/useHistoricalData.ts — Fetch historical OHLCV from QuestDB REST API
//
// Works in BOTH browser mode (localhost:3000) and Tauri mode.
// QuestDB exposes a REST API on port 9000 that accepts SQL queries.
//
// Endpoint: GET http://localhost:9000/exec?query=SELECT...&fmt=json

import { useState, useEffect, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';

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
  dataset: (string | number | null)[][];
  count: number;
}

interface UseHistoricalDataReturn {
  candles: HistoricalCandle[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

// Use the Next.js proxy rewrite to avoid CORS issues.
// next.config.ts maps /questdb/* → http://127.0.0.1:9000/*
const QUESTDB_URL = '/questdb/exec';

// Check if running in Tauri environment
const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

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
        // Fetch raw binary buffer from Rust backend
        const response = await invoke<number[] | Uint8Array>('get_historical_view', { symbol });
        const binaryBuffer = response instanceof Uint8Array ? response : new Uint8Array(response);
        const parsed = parseBincodeCandles(binaryBuffer);
        
        console.log(
          `[Historical Tauri] ${symbol}: ${parsed.length} candles loaded via IPC zero-latency buffer`
        );
        setCandles(parsed);
      } else {
        // --- WEB BROWSER PATH (REST API / JSON) ---
        const query = `SELECT ts, open, high, low, close, volume FROM historical_candles WHERE symbol = '${symbol}' ORDER BY ts ASC`;
        const url = `${QUESTDB_URL}?query=${encodeURIComponent(query)}&fmt=json`;

        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`QuestDB query failed: ${response.status} ${response.statusText}`);
        }

        const data: QuestDBResponse = await response.json();

        const parsed: HistoricalCandle[] = data.dataset.map((row) => {
          // row = [timestamp_string, open, high, low, close, volume]
          // QuestDB returns timestamps as ISO strings like "2024-01-15T00:00:00.000000Z"
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
        });

        console.log(
          `[Historical Web] ${symbol}: ${parsed.length} candles loaded from QuestDB`
        );

        setCandles(parsed);
      }
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
