'use client';

import { useEffect, useRef, useState, useMemo } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore, type PaneId } from '../../store/useChartUIStore';
import { createDatafeed, invalidateScrollBackCache } from '../../charting/datafeed';
import { useGhostLine } from '../../hooks/useGhostLine';
import type { IChartingLibraryWidget } from '../../charting/datafeedTypes';
import { TIMEFRAME_TO_RESOLUTION, getThemeOverrides } from '../../utils/tvThemeOverrides';
import { useTradingViewScript } from '../../hooks/useTradingViewScript';
import { getTvWidgetOptions } from '../../utils/tvWidgetOptions';
import { showIframeDropdown, injectIframeDropdownStyles } from '../../utils/iframeDropdown';
import { SVGS } from './toolbarIcons';

export interface TradingViewWidgetProps {
  symbolOverride?: string;
  timeframeOverride?: string;
  className?: string;
}

function syncButtonStates(doc: Document) {
  const theme = useChartUIStore.getState().theme;
  injectIframeDropdownStyles(doc, theme);

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
  const { ready: scriptReady, error: scriptError } = useTradingViewScript();
  const [widgetError, setWidgetError] = useState<string | null>(null);

  // ── Iframe Focus & Mouse Activation for Split Pane Selection ──────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handlePaneActivate = () => {
      const paneEl = container.closest('[data-pane-id]');
      if (paneEl) {
        const paneId = paneEl.getAttribute('data-pane-id') as PaneId;
        if (paneId && useChartUIStore.getState().activePaneId !== paneId) {
          useChartUIStore.getState().setActivePane(paneId);
        }
      }
    };

    container.addEventListener('mousedown', handlePaneActivate, true);
    container.addEventListener('pointerdown', handlePaneActivate, true);
    container.addEventListener('click', handlePaneActivate, true);

    const attachIframeListeners = () => {
      const iframe = container.querySelector('iframe');
      if (!iframe) return;
      try {
        const doc = iframe.contentDocument;
        if (doc) {
          doc.addEventListener('mousedown', handlePaneActivate, true);
          doc.addEventListener('pointerdown', handlePaneActivate, true);
          doc.addEventListener('click', handlePaneActivate, true);
          if (doc.defaultView) {
            doc.defaultView.addEventListener('focus', handlePaneActivate, true);
          }
        }
      } catch {}
    };

    attachIframeListeners();

    const iframe = container.querySelector('iframe');
    if (iframe) {
      iframe.addEventListener('load', attachIframeListeners);
    }

    const intervalId = setInterval(attachIframeListeners, 500);

    return () => {
      clearInterval(intervalId);
      container.removeEventListener('mousedown', handlePaneActivate, true);
      container.removeEventListener('pointerdown', handlePaneActivate, true);
      container.removeEventListener('click', handlePaneActivate, true);
      if (iframe) {
        iframe.removeEventListener('load', attachIframeListeners);
        try {
          const doc = iframe.contentDocument;
          if (doc) {
            doc.removeEventListener('mousedown', handlePaneActivate, true);
            doc.removeEventListener('pointerdown', handlePaneActivate, true);
            doc.removeEventListener('click', handlePaneActivate, true);
          }
        } catch {}
      }
    };
  }, [scriptReady, scriptError]);

  // ── Widget Initialization & Button Injection ─────────────────────────
  useEffect(() => {
    if (!scriptReady || !containerRef.current) return;
    if (!window.TradingView) {
      console.error('[TradingViewWidget] scriptReady=true but window.TradingView is undefined');
      setWidgetError('TradingView library loaded but widget constructor not found on window');
      return;
    }
    // Don't mount the widget until we have a real symbol to chart; otherwise
    // the widget boots with an empty ticker and shows a loading state.
    if (!activeSymbol) return;
    setWidgetError(null);

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
              const paneEl = containerRef.current?.closest('[data-pane-id]');
              if (paneEl) {
                const paneId = paneEl.getAttribute('data-pane-id') as PaneId;
                if (paneId) {
                  useChartUIStore.getState().setActivePane(paneId);
                  useChartUIStore.getState().setPaneSymbol(paneId, cleanSymbol);
                }
              } else {
                const currentSymbol = useTradeStore.getState().selectedSymbol;
                if (currentSymbol !== cleanSymbol) {
                  useTradeStore.getState().setSelectedSymbol(cleanSymbol);
                }
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
              { value: 'volume' as const, label: 'VWLR', description: 'Volume-weighted linear regression' },
              { value: 'curved' as const, label: 'VWEPR', description: 'Volume-weighted polynomial' },
              { value: 'forecast' as const, label: 'FCST', description: 'Volatility-aware forecaster' }
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
      setWidgetError(`Widget creation failed: ${err instanceof Error ? err.message : String(err)}`);
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
  }, [scriptReady, activeSymbol]);

  // Sync symbol changes
  const prevSymbolRef = useRef(activeSymbol);
  useEffect(() => {
    if (!activeSymbol) return;
    if (prevSymbolRef.current === activeSymbol) return;
    prevSymbolRef.current = activeSymbol;
    // Drop the per-symbol scroll-back cache so the new symbol starts fresh
    // (TV will call getBars with its initial window immediately).
    invalidateScrollBackCache(activeSymbol);
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
    const doc = containerRef.current?.querySelector('iframe')?.contentDocument;
    if (doc) {
      injectIframeDropdownStyles(doc, theme);
    }
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

  const displayError = scriptError || widgetError;

  return (
    <div className="relative h-full w-full min-h-0 overflow-hidden flex flex-col">
      {/* Loading state — script is still downloading */}
      {!scriptReady && !displayError && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-surface">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
          <span className="text-xs text-text-muted">Loading chart engine…</span>
        </div>
      )}
      {/* Error state — script or widget failed */}
      {displayError && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-surface px-6 text-center">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-amber-400">
            <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
            <path d="M12 9v4" /><path d="M12 17h.01" />
          </svg>
          <span className="text-xs font-semibold text-text-primary">Chart failed to load</span>
          <span className="max-w-md text-[10px] text-text-muted">{displayError}</span>
        </div>
      )}
      <div
        ref={containerRef}
        className={`flex-1 min-h-0 ${className}`}
        style={{ minHeight: '320px' }}
      />
    </div>
  );
}
