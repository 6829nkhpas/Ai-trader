'use client';

/**
 * TradingViewWidget — React wrapper for the TradingView Advanced Charts widget.
 *
 * Mounts the TV widget into a container div, wires it to the custom datafeed
 * adapter (which reads from the existing Zerodha/Kite pipeline), and syncs
 * symbol/timeframe changes from the Zustand stores.
 *
 * The widget owns its own toolbar, drawing tools, indicator search, chart type
 * selector, and timeframe selector — replacing the custom ChartToolsBar,
 * IndicatorManagerPanel, ChartTypeSelector, and timeframe dropdown.
 */

import React, { useEffect, useRef, useCallback } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { createDatafeed, RESOLUTION_TO_TIMEFRAME } from '../../charting/datafeed';
import { useGhostLine } from '../../hooks/useGhostLine';
import type {
  IChartingLibraryWidget,
  ChartingLibraryWidgetOptions,
  ResolutionString,
} from '../../charting/datafeedTypes';

// ── Resolution Mapping ────────────────────────────────────────────────────
/** Map store timeframe → TV resolution string. */
const TIMEFRAME_TO_RESOLUTION: Record<string, ResolutionString> = {
  '1m': '1', '2m': '2', '3m': '3', '4m': '4',
  '5m': '5', '10m': '10', '15m': '15', '30m': '30',
  '75m': '75', '125m': '125',
  '1h': '60', '1H': '60', '2h': '120', '3h': '180', '4h': '240',
  '1D': '1D', '1W': '1W', '1M': '1M',
};

// ── Theme overrides to match the institutional dark palette ───────────────
function getThemeOverrides(mode: 'dark' | 'light' = 'dark'): Record<string, string | number | boolean> {
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



// ── Props ─────────────────────────────────────────────────────────────────
export interface TradingViewWidgetProps {
  /** Per-pane symbol (split view); overrides the global selectedSymbol. */
  symbolOverride?: string;
  /** Per-pane timeframe (split view); overrides the global activeTimeframe. */
  timeframeOverride?: string;
  /** Additional CSS class for the container. */
  className?: string;
}

export default function TradingViewWidget({
  symbolOverride,
  timeframeOverride,
  className = '',
}: TradingViewWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<IChartingLibraryWidget | null>(null);
  const datafeedRef = useRef(createDatafeed());

  // ── Store selectors ──────────────────────────────────────────────────
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const activeDecision = useTradeStore((s) => s.activeDecision);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const predictiveSignals = useTradeStore((s) => s.predictiveSignals);
  const theme = useChartUIStore((s) => s.theme);
  const ghostLineMode = useChartUIStore((s) => s.ghostLineMode);

  // Derive active symbol
  const activeSymbol = React.useMemo(() => {
    if (symbolOverride) return symbolOverride.toUpperCase();
    if (selectedSymbol) return selectedSymbol.toUpperCase();
    const d = activeDecision ?? liveDecisions[liveDecisions.length - 1];
    return d?.symbol ?? 'RELIANCE';
  }, [symbolOverride, selectedSymbol, activeDecision, liveDecisions]);

  // Derive resolution
  const effectiveTimeframe = timeframeOverride ?? activeTimeframe ?? '15m';
  const resolution =
    TIMEFRAME_TO_RESOLUTION[effectiveTimeframe] ?? '15';

  // ── Script loader ────────────────────────────────────────────────────
  const loadTVScript = useCallback((): Promise<void> => {
    return new Promise((resolve, reject) => {
      if (window.TradingView) {
        resolve();
        return;
      }

      const existingScript = document.querySelector(
        'script[src="/static/charting_library/charting_library/charting_library.standalone.js"]',
      );
      if (existingScript) {
        existingScript.addEventListener('load', () => resolve());
        existingScript.addEventListener('error', () =>
          reject(new Error('Failed to load TradingView library')),
        );
        return;
      }

      const script = document.createElement('script');
      script.src = '/static/charting_library/charting_library/charting_library.standalone.js';
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () =>
        reject(new Error('Failed to load TradingView library'));
      document.head.appendChild(script);
    });
  }, []);

  // ── Widget Initialization ────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    let mounted = true;

    const initWidget = async () => {
      try {
        await loadTVScript();
      } catch (err) {
        console.error('[TradingViewWidget] Script load failed:', err);
        return;
      }

      if (!mounted || !containerRef.current || !window.TradingView) return;

      const widgetOptions: ChartingLibraryWidgetOptions = {
        container: containerRef.current,
        datafeed: datafeedRef.current,
        library_path: '/static/charting_library/charting_library/',
        symbol: `NSE:${activeSymbol}`,
        interval: resolution,
        timezone: 'Asia/Kolkata',
        theme: theme === 'light' ? 'light' : 'dark',
        locale: 'en',
        // custom_css_url is relative to library_path (charting_library/charting_library/)
        // so we go up two levels to reach public/static/tvThemeOverrides.css
        custom_css_url: '../../tvThemeOverrides.css',
        fullscreen: false,
        autosize: true,
        overrides: getThemeOverrides(theme),
        studies_overrides: {
          'volume.volume.color.0': '#ef4444',
          'volume.volume.color.1': '#10b981',
          'volume.volume.transparency': 50,
        },
        loading_screen: {
          backgroundColor: theme === 'light' ? '#f9fafb' : '#000000',
          foregroundColor: '#10b981',
        },
        disabled_features: [
          'use_localstorage_for_settings',
          'header_compare',
          'display_market_status',
          'popup_hints',
        ],
        enabled_features: [
          'study_templates',
          'side_toolbar_in_fullscreen_mode',
          'items_favoriting',
          'save_chart_properties_to_local_storage',
          // Advanced chart types
          'chart_style_hilo',
          'chart_style_range',
          'chart_style_renko',
          'chart_style_kagi',
          'chart_style_pnf',
          'chart_style_line_break',
          // Volume & Profile chart types
          'chart_style_vol_footprint',
          'chart_style_tpo',
          'chart_style_svp',
          'chart_style_vol_candle',
        ],
        debug: false,
        auto_save_delay: 5,
      };

      try {
        const tvWidget = new window.TradingView.widget(widgetOptions);
        widgetRef.current = tvWidget;

        tvWidget.onChartReady(() => {
          if (!mounted) return;
          console.log(
            `[TradingViewWidget] Chart ready: ${activeSymbol} @ ${resolution}`,
          );

          try {
            (tvWidget.activeChart() as any).onIntervalChanged().subscribe(null, (interval: string) => {
              const tf = RESOLUTION_TO_TIMEFRAME[interval];
              if (tf) {
                useTradeStore.getState().setActiveTimeframe(tf as any);
              }
            });
          } catch (e) {
            console.warn('[TradingViewWidget] Failed to subscribe to interval changes:', e);
          }
        });
      } catch (err) {
        console.error('[TradingViewWidget] Widget creation failed:', err);
      }
    };

    initWidget();

    return () => {
      mounted = false;
      if (widgetRef.current) {
        try {
          widgetRef.current.remove();
        } catch {
          // Widget may already be destroyed
        }
        widgetRef.current = null;
      }
    };
    // Only re-create widget on mount/unmount — symbol/timeframe changes
    // are handled reactively below via setSymbol/setResolution.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Sync symbol changes ──────────────────────────────────────────────
  const prevSymbolRef = useRef(activeSymbol);
  useEffect(() => {
    if (prevSymbolRef.current === activeSymbol) return;
    prevSymbolRef.current = activeSymbol;

    if (widgetRef.current) {
      try {
        widgetRef.current.setSymbol(
          `NSE:${activeSymbol}`,
          resolution,
        );
      } catch {
        // Widget not ready yet
      }
    }
  }, [activeSymbol, resolution]);

  // ── Sync timeframe changes ───────────────────────────────────────────
  const prevResolutionRef = useRef(resolution);
  useEffect(() => {
    if (prevResolutionRef.current === resolution) return;
    prevResolutionRef.current = resolution;

    if (widgetRef.current) {
      try {
        widgetRef.current.activeChart().setResolution(resolution);
      } catch {
        // Widget not ready yet
      }
    }
  }, [resolution]);

  // ── Sync theme changes ───────────────────────────────────────────────
  useEffect(() => {
    if (!widgetRef.current) return;
    const tvTheme = theme === 'light' ? 'light' : 'dark';
    try {
      widgetRef.current.changeTheme(tvTheme);
      // applyOverrides after a short delay to let changeTheme complete
      setTimeout(() => {
        if (!widgetRef.current) return;
        try {
          widgetRef.current.applyOverrides(getThemeOverrides(theme));
        } catch { /* widget not ready */ }
      }, 150);
    } catch {
      // Widget not ready
    }
  }, [theme]);

  // ── Predictive Ghost Line (VWEPR / OLS) ───────────────────────────────
  useGhostLine(widgetRef.current, activeSymbol, effectiveTimeframe);

  return (
    <div
      ref={containerRef}
      className={`h-full w-full min-h-0 overflow-hidden ${className}`}
      style={{ minHeight: '320px' }}
    />
  );
}
