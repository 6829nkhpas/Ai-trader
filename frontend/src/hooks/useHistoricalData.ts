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
        `[Historical] ${symbol}: ${parsed.length} candles loaded from QuestDB`
      );

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
