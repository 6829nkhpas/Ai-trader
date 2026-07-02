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
import { createDatafeed } from '../../charting/datafeed';
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
function getThemeOverrides(): Record<string, string | number | boolean> {
  // Hardcoded from the platform's CSS variables in globals.css
  // TV v31 uses different override property names than v28.
  return {
    // Chart pane background — matches --chart-bg (#0a0a0a)
    'paneProperties.backgroundType': 'solid',
    'paneProperties.background': '#0a0a0a',

    // Grid — matches --border-default (#1a1a1a)
    'paneProperties.vertGridProperties.color': '#1a1a1a',
    'paneProperties.horzGridProperties.color': '#1a1a1a',

    // Scale text — matches --text-muted (#9ca3af)
    'scalesProperties.textColor': '#9ca3af',
    'scalesProperties.lineColor': '#1a1a1a',
    'scalesProperties.backgroundColor': '#000000',

    // Candlestick colors — matches --candle-green (#10b981) / --candle-red (#ef4444)
    'mainSeriesProperties.candleStyle.upColor': '#10b981',
    'mainSeriesProperties.candleStyle.downColor': '#ef4444',
    'mainSeriesProperties.candleStyle.wickUpColor': '#10b981',
    'mainSeriesProperties.candleStyle.wickDownColor': '#ef4444',
    'mainSeriesProperties.candleStyle.borderUpColor': '#10b981',
    'mainSeriesProperties.candleStyle.borderDownColor': '#ef4444',
    'mainSeriesProperties.candleStyle.drawWick': true,
    'mainSeriesProperties.candleStyle.drawBorder': true,

    // Hollow candlestick fallback
    'mainSeriesProperties.hollowCandleStyle.upColor': '#10b981',
    'mainSeriesProperties.hollowCandleStyle.downColor': '#ef4444',
    'mainSeriesProperties.hollowCandleStyle.wickUpColor': '#10b981',
    'mainSeriesProperties.hollowCandleStyle.wickDownColor': '#ef4444',
    'mainSeriesProperties.hollowCandleStyle.borderUpColor': '#10b981',
    'mainSeriesProperties.hollowCandleStyle.borderDownColor': '#ef4444',

    // Bar style
    'mainSeriesProperties.barStyle.upColor': '#10b981',
    'mainSeriesProperties.barStyle.downColor': '#ef4444',

    // Area style
    'mainSeriesProperties.areaStyle.linecolor': '#10b981',
    'mainSeriesProperties.areaStyle.color1': 'rgba(16, 185, 129, 0.28)',
    'mainSeriesProperties.areaStyle.color2': 'rgba(16, 185, 129, 0.05)',

    // Line style
    'mainSeriesProperties.lineStyle.color': '#10b981',

    // Baseline style
    'mainSeriesProperties.baselineStyle.topLineColor': '#10b981',
    'mainSeriesProperties.baselineStyle.bottomLineColor': '#ef4444',

    // Volume colors
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
  const theme = useChartUIStore((s) => s.theme);

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
        theme: 'dark',
        locale: 'en',
        // custom_css_url is relative to library_path (charting_library/charting_library/)
        // so we go up two levels to reach public/static/tvThemeOverrides.css
        custom_css_url: '../../tvThemeOverrides.css',
        fullscreen: false,
        autosize: true,
        overrides: getThemeOverrides(),
        studies_overrides: {
          // Volume histogram colors
          'volume.volume.color.0': '#ef4444',
          'volume.volume.color.1': '#10b981',
          'volume.volume.transparency': 50,
        },
        loading_screen: {
          backgroundColor: '#000000',
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
    try {
      widgetRef.current.changeTheme('dark');
      widgetRef.current.applyOverrides(getThemeOverrides());
    } catch {
      // Widget not ready
    }
  }, [theme]);

  return (
    <div
      ref={containerRef}
      className={`h-full w-full min-h-0 overflow-hidden ${className}`}
      style={{ minHeight: '320px' }}
    />
  );
}
