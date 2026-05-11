import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import type { TradeProfile } from '../store/useTradeStore';

// ── Exported Types ────────────────────────────────────────────────────────

export type Timeframe = '1m' | '5m' | '10m' | '15m' | '1h' | '1H' | '1D';

export interface AlphaPredictiveChartProps {
  activeProfile?: TradeProfile;
  timeframe?: Timeframe;
  isExpanded?: boolean;
  onToggleExpand?: () => void;
}

/** Lightweight-charts compatible candle with numeric time. */
export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** Volume histogram bar. */
export interface VolumeBar {
  time: number;
  value: number;
  color: string;
}

/** EMA data point. */
export interface EmaPoint {
  time: number;
  value: number;
}

/** Bundled chart refs passed between hooks. */
export interface ChartRefs {
  chartRef: React.RefObject<IChartApi | null>;
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>;
  volumeSeriesRef: React.RefObject<ISeriesApi<'Histogram'> | null>;
  ghostLineRef: React.RefObject<ISeriesApi<'Line'> | null>;
  ema9SeriesRef: React.RefObject<ISeriesApi<'Line'> | null>;
  ema21SeriesRef: React.RefObject<ISeriesApi<'Line'> | null>;
  chartContainerRef: React.RefObject<HTMLDivElement | null>;
  drawingSeriesRef: React.MutableRefObject<ISeriesApi<'Line'>[]>;
  fibOverlayRef: React.RefObject<HTMLDivElement | null>;
}

// ── Constants ─────────────────────────────────────────────────────────────

export const TIMEFRAME_MS: Record<Timeframe, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '10m': 10 * 60_000,
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '1H': 60 * 60_000,
  '1D': 24 * 60 * 60_000,
};

// ── Institutional Dark-Mode Palette ──────────────────────────────────────
export const COLORS = {
  canvasBg: '#0F172A',
  text: '#CBD5E1',
  up: '#22c55e',
  down: '#ef4444',
  volumeUp: 'rgba(34, 197, 94, 0.35)',
  volumeDown: 'rgba(239, 68, 68, 0.30)',
  grid: 'rgba(51, 65, 85, 0.4)',
  crosshair: 'rgba(148, 163, 184, 0.5)',
  crosshairLabel: '#1E293B',
  border: '#334155',
  ghostLine: '#f59e0b',
  ema9: '#38bdf8',
  ema21: '#f472b6',
};
