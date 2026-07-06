import type { ResolutionString } from '../charting/datafeedTypes';

// ── Resolution Mapping ────────────────────────────────────────────────────
/** Map store timeframe → TV resolution string. */
export const TIMEFRAME_TO_RESOLUTION: Record<string, ResolutionString> = {
  '1m': '1', '2m': '2', '3m': '3', '4m': '4',
  '5m': '5', '10m': '10', '15m': '15', '30m': '30',
  '75m': '75', '125m': '125',
  '1h': '60', '1H': '60', '2h': '120', '3h': '180', '4h': '240',
  '1D': '1D', '1W': '1W', '1M': '1M',
};

// ── Theme overrides to match the institutional dark palette ───────────────
export function getThemeOverrides(mode: 'dark' | 'light' = 'dark'): Record<string, string | number | boolean> {
  const isDark = mode === 'dark';

  // Colors sourced from globals.css — :root (dark) and .light
  const bg       = isDark ? '#000000' : '#ffffff';
  const grid     = isDark ? '#1a1a1a' : '#f1f4f5';
  const text     = isDark ? '#9ca3af' : '#4a5568';
  const scaleBg  = isDark ? '#000000' : '#ffffff';
  const up       = '#10b981';
  const down     = '#ef4444';
  const areaFill1 = isDark ? 'rgba(16, 185, 129, 0.28)' : 'rgba(16, 185, 129, 0.15)';
  const areaFill2 = isDark ? 'rgba(16, 185, 129, 0.05)' : 'rgba(16, 185, 129, 0.02)';

  return {
    'paneProperties.backgroundType': 'solid',
    'paneProperties.background': bg,
    'paneProperties.vertGridProperties.color': grid,
    'paneProperties.horzGridProperties.color': grid,
    'scalesProperties.textColor': text,
    'scalesProperties.lineColor': grid,
    'scalesProperties.backgroundColor': scaleBg,

    'mainSeriesProperties.candleStyle.upColor': up,
    'mainSeriesProperties.candleStyle.downColor': down,
    'mainSeriesProperties.candleStyle.wickUpColor': up,
    'mainSeriesProperties.candleStyle.wickDownColor': down,
    'mainSeriesProperties.candleStyle.borderUpColor': up,
    'mainSeriesProperties.candleStyle.borderDownColor': down,
    'mainSeriesProperties.candleStyle.drawWick': true,
    'mainSeriesProperties.candleStyle.drawBorder': true,

    'mainSeriesProperties.hollowCandleStyle.upColor': up,
    'mainSeriesProperties.hollowCandleStyle.downColor': down,
    'mainSeriesProperties.hollowCandleStyle.wickUpColor': up,
    'mainSeriesProperties.hollowCandleStyle.wickDownColor': down,
    'mainSeriesProperties.hollowCandleStyle.borderUpColor': up,
    'mainSeriesProperties.hollowCandleStyle.borderDownColor': down,

    'mainSeriesProperties.barStyle.upColor': up,
    'mainSeriesProperties.barStyle.downColor': down,

    'mainSeriesProperties.areaStyle.linecolor': up,
    'mainSeriesProperties.areaStyle.color1': areaFill1,
    'mainSeriesProperties.areaStyle.color2': areaFill2,

    'mainSeriesProperties.lineStyle.color': up,

    'mainSeriesProperties.baselineStyle.topLineColor': up,
    'mainSeriesProperties.baselineStyle.bottomLineColor': down,

    'volumePaneSize': 'medium',
  };
}
