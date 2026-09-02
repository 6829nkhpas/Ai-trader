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
import { AlertTriangle } from 'lucide-react';
import { showIframeDropdown, injectIframeDropdownStyles } from '../../utils/iframeDropdown';
import { whenChartReady } from '../../charting/widgetReady';
import { SVGS } from './toolbarIcons';

/**
 * Point the widget at `theme` and re-assert our colour overrides.
 *
 * `changeTheme` is promise-returning in this library version. The previous code
 * fired it and then re-applied the overrides after a blind 150ms `setTimeout`,
 * so whenever the theme change settled later than that the overrides landed on
 * the OLD theme and were then overwritten — the chart kept the previous theme.
 * Chaining off the promise removes the race; the callback form is still handled
 * for older bundles.
 */
function applyChartTheme(widget: unknown, theme: 'light' | 'dark'): void {
  const w = widget as {
    changeTheme?: (t: string) => unknown;
    applyOverrides?: (o: Record<string, unknown>) => void;
  } | null;
  if (!w || typeof w.changeTheme !== 'function') return;

  const overrides = () => {
    try {
      w.applyOverrides?.(getThemeOverrides(theme));
    } catch (err) {
      // Worth seeing: a silent failure here is exactly how the "chart stays in
      // the old theme" bug hid for so long.
      console.warn('[TradingViewWidget] applyOverrides failed:', err);
    }
  };

  try {
    const result = w.changeTheme(theme === 'light' ? 'light' : 'dark');
    if (result && typeof (result as Promise<void>).then === 'function') {
      (result as Promise<void>).then(overrides, overrides);
    } else {
      overrides();
    }
  } catch (err) {
    console.warn('[TradingViewWidget] changeTheme failed:', err);
  }
}

export interface TradingViewWidgetProps {
  symbolOverride?: string;
  timeframeOverride?: string;
  className?: string;
}

function syncButtonStates(doc: Document) {
  // No theme argument: the injector reads the live tokens off the parent document,
  // so it cannot be handed a value that disagrees with what is on screen.
  injectIframeDropdownStyles(doc);

  const ghostLineMode = useChartUIStore.getState().ghostLineMode;
  const splitView = useChartUIStore.getState().splitView;

  // The custom Standard / Vol Profile / Footprint button used to be synced here.
  // Removed with the button itself — see the note at its creation site below.

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

      whenChartReady(tvWidget, () => {
        // Reconcile the theme now that the chart exists.
        //
        // TradingView restores its own saved chart properties from
        // localStorage (`save_chart_properties_to_local_storage` /
        // `load_last_chart`), which can carry the colours of whatever theme was
        // last used and override the `theme` we passed at construction. The
        // theme effect below only runs when `theme` CHANGES, so on a fresh load
        // nothing corrected those restored properties — the shell rendered dark
        // while the candles stayed light. Applying the overrides here makes the
        // chart match the store on every mount, not just on a toggle.
        applyChartTheme(tvWidget, useChartUIStore.getState().theme);

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
          // ── Chart-mode selector: REMOVED for now ────────────────────────────
          // The custom toolbar button offering Standard / Vol Profile / Footprint
          // used to be created here. It was already vestigial: ChartSurface stopped
          // branching on `chartMode` once TradingView Advanced Charts v31 began
          // handling Volume Footprint, TPO, SVP and Volume Candle natively through
          // its OWN chart-type selector, and `FootprintChart.tsx` is no longer
          // mounted by anything. So the button wrote a store value that changed
          // nothing on screen except its own icon — two selectors, only one of
          // which worked.
          //
          // `chartMode`, `setChartMode` and the ChartMode type are deliberately
          // LEFT IN PLACE (store, persistence and their tests are untouched), so
          // restoring this is re-adding the button, not unpicking a data model.
          // Use TradingView's built-in chart-type control in the meantime.

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
    // NOTE: `activeSymbol` is deliberately NOT a dependency.
    //
    // It used to be, which meant every symbol change destroyed the widget
    // (`remove()`) and constructed a brand new one. Two bugs came out of that:
    //
    //  · The chart "reloaded" constantly. When no symbol is explicitly
    //    selected, `activeSymbol` falls back to the tail of the streaming
    //    `liveDecisions` array, so each incoming decision changed it and
    //    rebuilt the whole widget.
    //  · It opened the tear-down race behind the GhostLine TypeError:
    //    `remove()` runs synchronously in cleanup but `setWidgetState(null)`
    //    only lands on the next render, so dependent effects kept operating on
    //    a gutted widget.
    //
    // Symbol changes are already handled incrementally by the effect below via
    // `activeChart().setSymbol`, which is what the library wants anyway. The
    // widget is now built once per mount. `activeSymbol` is still read on the
    // first run to pick the initial ticker — that is intentional and correct,
    // since a later change is applied by the sync effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scriptReady]);

  // Sync symbol changes
  const prevSymbolRef = useRef(activeSymbol);
  const prevResolutionRef = useRef(resolution);
  useEffect(() => {
    if (!activeSymbol) return;
    if (prevSymbolRef.current === activeSymbol) return;
    prevSymbolRef.current = activeSymbol;
    // Drop the per-symbol scroll-back cache so the new symbol starts fresh
    // (TV will call getBars with its initial window immediately).
    invalidateScrollBackCache(activeSymbol);

    const widget = widgetRef.current;
    if (!widget) return;

    const sym = activeSymbol.toUpperCase();
    const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
    const exchange = isFno ? 'NFO' : 'NSE';
    const ticker = `${exchange}:${sym}`;

    // `widget.setSymbol(...)` is deprecated in favour of
    // `widget.activeChart().setSymbol(...)` (and the deprecated overload also
    // requires a third `callback` argument that was never being passed). Go
    // through the chart API, and set the resolution separately — the chart-level
    // `setSymbol` takes only the ticker.
    whenChartReady(
      widget,
      () => {
        const chart = widget.activeChart() as {
          setSymbol: (s: string) => void;
          setResolution?: (r: string) => void;
        };
        chart.setSymbol(ticker);
        if (prevResolutionRef.current !== resolution) {
          prevResolutionRef.current = resolution;
          chart.setResolution?.(resolution);
        }
      },
      () => widgetRef.current !== widget,
      'TradingViewWidget',
    );
  }, [activeSymbol, resolution]);

  // Sync timeframe changes
  useEffect(() => {
    if (prevResolutionRef.current === resolution) return;
    prevResolutionRef.current = resolution;
    const widget = widgetRef.current;
    if (!widget) return;
    whenChartReady(
      widget,
      () => widget.activeChart().setResolution(resolution),
      () => widgetRef.current !== widget,
      'TradingViewWidget',
    );
  }, [resolution]);

  // Sync theme changes
  useEffect(() => {
    const doc = containerRef.current?.querySelector('iframe')?.contentDocument;
    if (doc) {
      // Re-read the tokens now that `.light` has been added or removed. The
      // `theme` dep is what schedules this; the colours themselves come from the
      // document, not from `theme`.
      injectIframeDropdownStyles(doc);
    }
    const widget = widgetRef.current;
    if (!widget) return;
    // `applyChartTheme` chains the override re-apply off `changeTheme`'s promise
    // instead of guessing with a timeout — see its doc comment.
    whenChartReady(
      widget,
      () => applyChartTheme(widget, theme),
      () => widgetRef.current !== widget,
      'TradingViewWidget',
    );
  }, [theme]);

  // React state synchronization to iframe buttons
  useEffect(() => {
    const doc = containerRef.current?.querySelector('iframe')?.contentDocument;
    if (doc && buttonsCreated) {
      syncButtonStates(doc);
    }
    // `chartMode` was a dependency here purely to re-render the removed button's
    // icon. Dropped with it — nothing in syncButtonStates reads it any more.
  }, [ghostLineMode, splitView, sidebarOpen, buttonsCreated]);

  useGhostLine(widgetState, activeSymbol, effectiveTimeframe);

  const displayError = scriptError || widgetError;

  return (
    <div className="relative h-full w-full min-h-0 overflow-hidden flex flex-col">
      {/* Loading state — script is still downloading */}
      {!scriptReady && !displayError && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-[#0a0a0a]">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
          <span className="text-xs text-text-muted">Loading chart engine…</span>
        </div>
      )}
      {/* Error state — script or widget failed */}
      {displayError && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-[#0a0a0a] px-6 text-center">
          <span className="flex items-center gap-1.5 text-sm font-bold text-amber-400">
            <AlertTriangle size={14} className="shrink-0" />
            Chart failed to load
          </span>
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
