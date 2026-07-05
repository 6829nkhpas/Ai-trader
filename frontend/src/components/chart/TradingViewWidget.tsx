'use client';

import { useEffect, useRef, useState, useMemo } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { createDatafeed } from '../../charting/datafeed';
import { useGhostLine } from '../../hooks/useGhostLine';
import type { IChartingLibraryWidget } from '../../charting/datafeedTypes';
import { TIMEFRAME_TO_RESOLUTION, getThemeOverrides } from '../../utils/tvThemeOverrides';
import { useTradingViewScript } from '../../hooks/useTradingViewScript';
import { getTvWidgetOptions } from '../../utils/tvWidgetOptions';
import { showIframeDropdown } from '../../utils/iframeDropdown';
import { SVGS } from './toolbarIcons';

export interface TradingViewWidgetProps {
  symbolOverride?: string;
  timeframeOverride?: string;
  className?: string;
}

function syncButtonStates(doc: Document) {
  const chartMode = useTradeStore.getState().chartMode;
  const ghostLineMode = useChartUIStore.getState().ghostLineMode;
  const splitView = useChartUIStore.getState().splitView;

  const chartModeBtn = doc.getElementById('tv-btn-chart-mode');
  if (chartModeBtn) {
    if (chartMode === 'STANDARD') {
      chartModeBtn.innerHTML = SVGS.standard;
    } else if (chartMode === 'VOLUME_PROFILE') {
      chartModeBtn.innerHTML = SVGS.volProfile;
    } else {
      chartModeBtn.innerHTML = SVGS.footprint;
    }
  }

  const ghostLineBtn = doc.getElementById('tv-btn-ghost-line');
  if (ghostLineBtn) {
    ghostLineBtn.innerHTML = SVGS.ghostLine;
    if (ghostLineMode === 'curved') {
      ghostLineBtn.classList.add('active');
    } else {
      ghostLineBtn.classList.remove('active');
    }
  }

  const splitViewBtn = doc.getElementById('tv-btn-split-view');
  if (splitViewBtn) {
    splitViewBtn.innerHTML = splitView ? SVGS.splitView : SVGS.singleView;
    if (splitView) {
      splitViewBtn.classList.add('active');
    } else {
      splitViewBtn.classList.remove('active');
    }
  }
}

export default function TradingViewWidget({
  symbolOverride,
  timeframeOverride,
  className = '',
}: TradingViewWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<IChartingLibraryWidget | null>(null);
  const [widgetState, setWidgetState] = useState<IChartingLibraryWidget | null>(null);
  const [buttonsCreated, setButtonsCreated] = useState(false);
  const datafeedRef = useRef(createDatafeed());

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const activeDecision = useTradeStore((s) => s.activeDecision);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const chartMode = useTradeStore((s) => s.chartMode);

  const theme = useChartUIStore((s) => s.theme);
  const isFullscreen = useChartUIStore((s) => s.isFullscreen);
  const sidebarOpen = useChartUIStore((s) => s.sidebarOpen);
  const ghostLineMode = useChartUIStore((s) => s.ghostLineMode);
  const splitView = useChartUIStore((s) => s.splitView);

  const activeSymbol = useMemo(() => {
    if (symbolOverride) return symbolOverride.toUpperCase();
    if (selectedSymbol) return selectedSymbol.toUpperCase();
    return (activeDecision ?? liveDecisions[liveDecisions.length - 1])?.symbol ?? 'RELIANCE';
  }, [symbolOverride, selectedSymbol, activeDecision, liveDecisions]);

  const effectiveTimeframe = timeframeOverride ?? activeTimeframe ?? '15m';
  const resolution = TIMEFRAME_TO_RESOLUTION[effectiveTimeframe] ?? '15';
  const scriptReady = useTradingViewScript();

  // ── Widget Initialization & Button Injection ─────────────────────────
  useEffect(() => {
    if (!scriptReady || !containerRef.current || !window.TradingView) return;

    const widgetOptions = getTvWidgetOptions({
      container: containerRef.current,
      datafeed: datafeedRef.current,
      activeSymbol,
      resolution,
      theme,
    });

    try {
      const tvWidget = new window.TradingView.widget(widgetOptions);
      widgetRef.current = tvWidget;
      setWidgetState(tvWidget);

      tvWidget.onChartReady(() => {
        // Listen to symbol changes from the TV search box
        try {
          const chartApi = tvWidget.activeChart() as any;
          chartApi.onSymbolChanged().subscribe(null, () => {
            const fullSymbol = chartApi.symbol();
            if (fullSymbol && fullSymbol !== '---') {
              const cleanSymbol = fullSymbol.includes(':') ? fullSymbol.split(':')[1] : fullSymbol;
              const currentSymbol = useTradeStore.getState().selectedSymbol;
              if (currentSymbol !== cleanSymbol) {
                useTradeStore.getState().setSelectedSymbol(cleanSymbol);
              }
            }
          });
        } catch (err) {
          console.warn('[TradingViewWidget] Failed to subscribe to onSymbolChanged:', err);
        }

        const iframe = containerRef.current?.querySelector('iframe');
        const doc = iframe?.contentDocument;
        if (!doc) return;

        try {
          const chartModeBtn = (tvWidget as any).createButton();
          chartModeBtn.id = 'tv-btn-chart-mode';
          chartModeBtn.className = 'tv-custom-toolbar-btn';
          chartModeBtn.title = 'Chart Mode';
          chartModeBtn.addEventListener('click', () => {
            const currentMode = useTradeStore.getState().chartMode;
            showIframeDropdown(chartModeBtn, [
              { value: 'STANDARD' as const, label: 'Standard' },
              { value: 'VOLUME_PROFILE' as const, label: 'Vol Profile' },
              { value: 'FOOTPRINT' as const, label: 'Footprint' }
            ], currentMode, (v) => {
              useTradeStore.getState().setChartMode(v);
              syncButtonStates(doc);
            }, doc);
          });

          const ghostLineBtn = (tvWidget as any).createButton();
          ghostLineBtn.id = 'tv-btn-ghost-line';
          ghostLineBtn.className = 'tv-custom-toolbar-btn';
          ghostLineBtn.title = 'Projection Engine';
          ghostLineBtn.addEventListener('click', () => {
            const currentMode = useChartUIStore.getState().ghostLineMode;
            showIframeDropdown(ghostLineBtn, [
              { value: 'linear' as const, label: 'OLS', description: 'Linear regression baseline' },
              { value: 'curved' as const, label: 'VWEPR', description: 'Volume-weighted polynomial' }
            ], currentMode, (v) => {
              useChartUIStore.getState().setGhostLineMode(v);
              syncButtonStates(doc);
            }, doc);
          });

          let splitViewBtn: HTMLElement | undefined;
          const activeProfile = useTradeStore.getState().activeProfile;
          if (activeProfile === 'INTRADAY' || activeProfile === 'FNO') {
            const btn = (tvWidget as any).createButton();
            btn.id = 'tv-btn-split-view';
            btn.className = 'tv-custom-toolbar-btn';
            btn.title = 'Chart Layout';
            btn.addEventListener('click', () => {
              const currentVal = useChartUIStore.getState().splitView;
              showIframeDropdown(btn, [
                { value: false, label: 'Single Pane' },
                { value: true, label: 'Split Pane' }
              ], currentVal, (v) => {
                useChartUIStore.getState().setSplitView(v);
                syncButtonStates(doc);
              }, doc);
            });
            splitViewBtn = btn;
          }



          setButtonsCreated(true);
          syncButtonStates(doc);
        } catch (err) {
          console.error('[TradingViewWidget] Custom button registration failed:', err);
        }
      });
    } catch (err) {
      console.error('[TradingViewWidget] Widget creation failed:', err);
    }

    return () => {
      if (widgetRef.current) {
        try {
          widgetRef.current.remove();
        } catch {}
        widgetRef.current = null;
        setWidgetState(null);
        setButtonsCreated(false);
      }
    };
  }, [scriptReady]);

  // Sync symbol changes
  const prevSymbolRef = useRef(activeSymbol);
  useEffect(() => {
    if (prevSymbolRef.current === activeSymbol) return;
    prevSymbolRef.current = activeSymbol;
    if (widgetRef.current) {
      try {
        const sym = activeSymbol.toUpperCase();
        const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
        const exchange = isFno ? 'NFO' : 'NSE';
        widgetRef.current.setSymbol(`${exchange}:${activeSymbol}`, resolution);
      } catch {}
    }
  }, [activeSymbol, resolution]);

  // Sync timeframe changes
  const prevResolutionRef = useRef(resolution);
  useEffect(() => {
    if (prevResolutionRef.current === resolution) return;
    prevResolutionRef.current = resolution;
    if (widgetRef.current) {
      try {
        widgetRef.current.activeChart().setResolution(resolution);
      } catch {}
    }
  }, [resolution]);

  // Sync theme changes
  useEffect(() => {
    if (!widgetRef.current) return;
    try {
      widgetRef.current.changeTheme(theme === 'light' ? 'light' : 'dark');
      setTimeout(() => {
        if (widgetRef.current) {
          try {
            widgetRef.current.applyOverrides(getThemeOverrides(theme));
          } catch {}
        }
      }, 150);
    } catch {}
  }, [theme]);

  // React state synchronization to iframe buttons
  useEffect(() => {
    const doc = containerRef.current?.querySelector('iframe')?.contentDocument;
    if (doc && buttonsCreated) {
      syncButtonStates(doc);
    }
  }, [chartMode, ghostLineMode, splitView, sidebarOpen, buttonsCreated]);

  useGhostLine(widgetState, activeSymbol, effectiveTimeframe);

  return (
    <div className="relative h-full w-full min-h-0 overflow-hidden flex flex-col">
      <div
        ref={containerRef}
        className={`flex-1 min-h-0 ${className}`}
        style={{ minHeight: '320px' }}
      />
    </div>
  );
}
