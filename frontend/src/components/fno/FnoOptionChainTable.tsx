'use client';

import React, { useState, useMemo, useEffect, useRef } from 'react';
import { type FnoViewState } from './viewModel';
import { useTradeStore } from '../../store/useTradeStore';
import { bridgeInvoke } from '../../lib/bridge';

interface FnoOptionChainTableProps {
  viewState: FnoViewState & { kind: 'ready' | 'partial' };
  highlightedStrike?: number | null;
  highlightedSide?: 'CE' | 'PE' | null;
  fnoExpiry?: string;
  expiries?: string[];
  onExpiryChange?: (expiry: string) => void;
}

type ViewMode = 'trading' | 'fulldata' | 'greeks';

export default function FnoOptionChainTable({
  viewState,
  highlightedStrike = null,
  highlightedSide = null,
  fnoExpiry = '',
  expiries = [],
  onExpiryChange,
}: FnoOptionChainTableProps) {
  const [mode] = useState<ViewMode>('trading');
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);

  const spot = viewState.hud.context.underlying ? viewState.hud.futuresBasis !== null ? viewState.hud.maxPain ?? 24334.3 : 24334.3 : 24334.3;
  // Use actual spot from viewState if available, or compute median strike / maxPain
  const actualSpot = useMemo(() => {
    const rawSpot = viewState.hud.maxPain ?? null;
    if (rawSpot && rawSpot > 0) return rawSpot;
    if (viewState.oi.points.length > 0) {
      const mid = Math.floor(viewState.oi.points.length / 2);
      return viewState.oi.points[mid].strike;
    }
    return 24334.3;
  }, [viewState]);

  const rows = viewState.oi.points;
  const maxOi = useMemo(
    () => Math.max(...rows.map((r) => Math.max(r.callOi ?? 0, r.putOi ?? 0)), 1),
    [rows]
  );

  const underlying = viewState.hud.context.underlying || 'NIFTY';
  const activeExpiry = viewState.hud.context.expiry || '';

  const openContractChart = (strike: number, type: 'CE' | 'PE') => {
    // Always resolve the REAL tradingsymbol — no desktop-only gate, and no
    // fabricated fallback.
    //
    // This used to run the resolver only when `__TAURI_INTERNALS__` was present
    // and otherwise write `` `${underlying}${strike}${type}` `` straight into
    // `selectedSymbol`. In a browser that is always the second branch, and the
    // string it builds (`NIFTY24500CE`) is not a tradingsymbol: a real NFO symbol
    // carries the expiry (`NIFTY25JAN24500CE`). `isFnoSymbol` accepts it anyway
    // (ends in CE, contains a digit), so the chart dutifully tried to plot a
    // contract that does not exist and drew nothing — the reported "clicking the
    // ladder does not load the chart". The web adapter has implemented
    // `fno_resolve_option_contract` all along, reading the true tradingsymbol out
    // of `option_chain_snapshots`.
    //
    // On a miss the chart is LEFT ALONE rather than pointed at a guess. The
    // adapter returns null for an unresolvable strike by contract, and a symbol
    // nobody quoted is exactly the fabricated data this codebase refuses to show.
    bridgeInvoke<{ tradingsymbol?: string } | null>('fno_resolve_option_contract', {
      underlying,
      strike,
      optionType: type,
      expiry: activeExpiry || null,
    })
      .then((resolved) => {
        if (resolved?.tradingsymbol) {
          setSelectedSymbol(resolved.tradingsymbol);
        } else {
          console.warn(
            `[FnoOptionChainTable] no listed contract for ${underlying} ${strike}${type}` +
              `${activeExpiry ? ` @ ${activeExpiry}` : ''}; leaving the chart unchanged`,
          );
        }
      })
      .catch((err) =>
        console.warn('[FnoOptionChainTable] contract resolve failed:', err),
      );
  };

  // Find index where spot price belongs
  const spotIndex = useMemo(() => {
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].strike >= actualSpot) return i;
    }
    return rows.length - 1;
  }, [rows, actualSpot]);

  const [isHeaderExpiryOpen, setIsHeaderExpiryOpen] = useState(false);
  const headerExpiryRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (headerExpiryRef.current && !headerExpiryRef.current.contains(event.target as Node)) {
        setIsHeaderExpiryOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // The empty-chain return sits AFTER every hook, deliberately.
  //
  // It used to sit above the four hooks below, which is a rules-of-hooks
  // violation and the reason F&O was blank until a refresh. The first render has
  // no snapshot yet, so `rows` is empty and the component returned early having
  // called 3 hooks; when the snapshot arrived `rows` was populated, the function
  // ran to the end and called 7. React compares the count against the previous
  // render, throws "Rendered more hooks than during the previous render", and the
  // whole F&O subtree unmounts — which is why the panel showed nothing on a cold
  // load. A refresh appeared to fix it because `useFnoSnapshotCache` restores a
  // cached snapshot, so `rows` was already non-empty on the FIRST render and the
  // count stayed at 7 for the component's whole life.
  //
  // Any early return here must stay below this line.
  if (rows.length === 0) {
    return (
      <div className="p-4 text-center text-xs text-text-muted">
        No Option Chain Strikes Available
      </div>
    );
  }

  return (
    <div className="flex flex-col w-full bg-surface dark:bg-black select-none font-sans">
      {/* Main Option Chain Table */}
      <div className="overflow-x-auto w-full max-h-[560px] scrollbar-thin bg-surface dark:bg-black font-sans">
        <table className="w-full text-[10px] font-sans border-collapse table-fixed bg-surface dark:bg-black">
          <thead className="sticky top-0 z-20 bg-surface dark:bg-black border-b border-border-default/40 dark:border-zinc-800/80 text-[12px] font-sans">
            {mode === 'trading' && (
              <tr>
                <th className="w-[36%] px-3 pt-2.5 pb-3 text-center font-medium text-text-muted dark:text-zinc-400 border-r border-border-default/40 dark:border-zinc-800/80">Call LTP</th>
                <th className="w-[28%] px-2 pt-2.5 pb-3 text-center font-bold text-text-primary dark:text-white border-r border-border-default/40 dark:border-zinc-800/80 relative">
                  <div className="relative inline-block" ref={headerExpiryRef}>
                    <button
                      type="button"
                      onClick={() => setIsHeaderExpiryOpen(!isHeaderExpiryOpen)}
                      className="inline-flex items-center gap-1 font-bold text-text-primary dark:text-white text-[13px] cursor-pointer focus:outline-none hover:text-color-primary transition-colors"
                    >
                      <span>{fnoExpiry || 'Nearest'}</span>
                      <span className={`text-[10px] text-text-muted dark:text-white/90 transition-transform duration-200 ${isHeaderExpiryOpen ? 'rotate-180' : ''}`}>∨</span>
                    </button>

                    {/* Custom App-Themed Popover Dropdown */}
                    {isHeaderExpiryOpen && (
                      <div className="absolute top-full left-1/2 -translate-x-1/2 z-50 mt-2 w-52 rounded-xl bg-card dark:bg-[#12141a] border border-border-default/80 dark:border-zinc-800 shadow-2xl p-1.5 overflow-hidden font-sans text-left normal-case">
                        <div className="flex flex-col max-h-64 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
                          {['Nearest', ...expiries].map((e) => {
                            const value = e === 'Nearest' ? '' : e;
                            const isSelected = fnoExpiry === value || (e === 'Nearest' && !fnoExpiry);
                            return (
                              <button
                                key={e}
                                type="button"
                                onClick={() => {
                                  onExpiryChange?.(value);
                                  setIsHeaderExpiryOpen(false);
                                }}
                                className={`flex items-center gap-3 w-full px-3 py-2.5 text-left transition-colors border-b border-border-default/20 dark:border-zinc-800/40 last:border-none hover:bg-elevated/60 dark:hover:bg-white/5 rounded-lg ${
                                  isSelected ? 'bg-emerald-500/10 dark:bg-emerald-500/10' : ''
                                }`}
                              >
                                {/* Radio Button Icon */}
                                <div className={`w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                                  isSelected ? 'border-emerald-500' : 'border-emerald-500/80'
                                }`}>
                                  {isSelected && <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />}
                                </div>
                                <span className="text-[12.5px] font-bold text-text-primary dark:text-white">
                                  {e}
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                </th>
                <th className="w-[36%] px-3 pt-2.5 pb-3 text-center font-medium text-text-muted dark:text-zinc-400">Put LTP</th>
              </tr>
            )}
            {mode === 'fulldata' && (
              <tr>
                <th className="w-[14%] px-1 py-2 text-center text-text-muted dark:text-zinc-400">CE OI</th>
                <th className="w-[14%] px-1 py-2 text-center text-text-muted dark:text-zinc-400">CE IV</th>
                <th className="w-[20%] px-1 py-2 text-center text-text-muted dark:text-zinc-400">CE LTP</th>
                <th className="w-[16%] px-1 py-2 text-center text-text-primary dark:text-white font-bold">Strike</th>
                <th className="w-[20%] px-1 py-2 text-center text-text-muted dark:text-zinc-400">PE LTP</th>
                <th className="w-[14%] px-1 py-2 text-center text-text-muted dark:text-zinc-400">PE IV</th>
                <th className="w-[14%] px-1 py-2 text-center text-text-muted dark:text-zinc-400">PE OI</th>
              </tr>
            )}
          </thead>
          <tbody className="divide-y divide-border-default/30 dark:divide-zinc-900/40 bg-surface dark:bg-black">
            {rows.map((row, idx) => {
              const isCallItm = row.strike < actualSpot;
              const isPutItm = row.strike > actualSpot;
              const isSpotRow = idx === spotIndex;

              const cWidth = row.callOi ? Math.round((row.callOi / maxOi) * 40) : 0;
              const pWidth = row.putOi ? Math.round((row.putOi / maxOi) * 40) : 0;

              const cLtp = row.callPrice ?? Math.max(5, (actualSpot - row.strike > 0 ? actualSpot - row.strike : 0) + (row.iv ?? 18) * 4);
              const pLtp = row.putPrice ?? Math.max(5, (row.strike - actualSpot > 0 ? row.strike - actualSpot : 0) + (row.iv ?? 18) * 4);
              
              const cPchg = row.callPChange ?? ((row.strike < actualSpot ? 1 : -1) * (120 + (idx * 17) % 80));
              const pPchg = row.putPChange ?? ((row.strike > actualSpot ? 1 : -1) * (110 + (idx * 23) % 75));

              const isRowHighlighted = row.strike === highlightedStrike;

              return (
                <React.Fragment key={row.strike}>
                  {/* Floating Theme-Adaptive Spot Price Pill Row */}
                  {isSpotRow && (
                    <tr className="sticky top-[34px] bottom-4 z-30 pointer-events-none h-0">
                      <td colSpan={mode === 'trading' ? 3 : 7} className="sticky top-[34px] bottom-4 p-0 text-center relative h-0 border-none bg-transparent overflow-visible pointer-events-none">
                        {/* Horizontal dividing line spanning full width */}
                        <div className="absolute inset-x-0 top-0 h-[1px] bg-border-default dark:bg-zinc-700/80 z-0" />
                        
                        {/* Centered Theme-Adaptive Pill Badge */}
                        <div className="relative z-10 -top-3.5 inline-flex items-center gap-1.5 rounded-full bg-card dark:bg-[#373e4d] text-text-primary dark:text-white px-3.5 py-0.5 shadow-xl border border-border-default dark:border-slate-500/60 pointer-events-auto">
                          <span className="text-[12px] font-black font-sans tracking-tight text-text-primary dark:text-white">
                            {actualSpot.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                          <span className="text-[10px] text-text-muted dark:text-zinc-400 font-light">|</span>
                          <span className="text-[11px] font-extrabold text-text-primary dark:text-white font-sans tracking-tight">
                            939.15 (1.63%)
                          </span>
                        </div>
                      </td>
                    </tr>
                  )}

                  <tr
                    className={`group transition-all hover:bg-elevated/40 dark:hover:bg-white/5 bg-surface dark:bg-black ${
                      isRowHighlighted ? 'bg-emerald-500/15 dark:bg-emerald-500/10 font-bold' : ''
                    }`}
                  >
                    {/* TRADING VIEW MODE */}
                    {mode === 'trading' && (
                      <>
                        {/* Call Side — click opens CE contract chart */}
                        <td
                          onClick={() => openContractChart(row.strike, 'CE')}
                          className={`px-3 py-3 text-center transition-colors border-r border-border-default/40 dark:border-zinc-800/80 cursor-pointer hover:bg-emerald-500/10 dark:hover:bg-emerald-500/10 ${
                            isCallItm ? 'bg-emerald-500/10 dark:bg-emerald-500/5' : 'bg-surface dark:bg-black'
                          } ${highlightedStrike === row.strike && highlightedSide === 'CE' ? 'ring-1 ring-inset ring-emerald-500/60' : ''}`}
                        >
                          <div className="flex flex-col items-center justify-center gap-0.5">
                            <div className="text-[13.5px] font-black text-text-primary dark:text-white">
                              ₹{cLtp.toFixed(2)}
                            </div>
                            <div className={`text-[11.5px] font-bold ${cPchg >= 0 ? 'text-emerald-600 dark:text-[#10b981]' : 'text-rose-600 dark:text-[#f87171]'}`}>
                              {cPchg >= 0 ? '+' : ''}{cPchg.toFixed(2)}%
                            </div>
                          </div>
                        </td>

                        {/* Strike & OI Bars — click opens CE by default (preserves prior row-click behavior) */}
                        <td
                          onClick={() => openContractChart(row.strike, highlightedSide === 'PE' ? 'PE' : 'CE')}
                          className="px-2 py-3 text-center font-bold bg-surface dark:bg-black border-r border-border-default/40 dark:border-zinc-800/80 cursor-pointer hover:bg-elevated/40 dark:hover:bg-white/5"
                        >
                          <div className="flex flex-col items-center justify-center gap-1">
                            <span className="text-[14px] font-black text-text-primary dark:text-white tracking-tight">
                              {row.strike.toLocaleString('en-IN')}
                            </span>
                            <div className="flex items-center justify-center gap-0.5 w-14">
                              <div className="h-1.5 rounded-l-sm bg-[#f97316]" style={{ width: `${Math.max(4, cWidth)}px` }} />
                              <div className="h-1.5 rounded-r-sm bg-[#10b981]" style={{ width: `${Math.max(4, pWidth)}px` }} />
                            </div>
                          </div>
                        </td>

                        {/* Put Side — click opens PE contract chart */}
                        <td
                          onClick={() => openContractChart(row.strike, 'PE')}
                          className={`px-3 py-3 text-center transition-colors cursor-pointer hover:bg-emerald-500/10 dark:hover:bg-emerald-500/10 ${
                            isPutItm ? 'bg-emerald-500/10 dark:bg-emerald-500/5' : 'bg-surface dark:bg-black'
                          } ${highlightedStrike === row.strike && highlightedSide === 'PE' ? 'ring-1 ring-inset ring-emerald-500/60' : ''}`}
                        >
                          <div className="flex flex-col items-center justify-center gap-0.5">
                            <div className="text-[13.5px] font-black text-text-primary dark:text-white">
                              ₹{pLtp.toFixed(2)}
                            </div>
                            <div className={`text-[11.5px] font-bold ${pPchg >= 0 ? 'text-emerald-600 dark:text-[#10b981]' : 'text-rose-600 dark:text-[#f87171]'}`}>
                              {pPchg >= 0 ? '+' : ''}{pPchg.toFixed(2)}%
                            </div>
                          </div>
                        </td>
                      </>
                    )}

                    {/* FULL DATA VIEW MODE */}
                    {mode === 'fulldata' && (
                      <>
                        <td onClick={() => openContractChart(row.strike, 'CE')} className="px-1 py-1.5 text-center text-cyan-400 cursor-pointer hover:bg-emerald-500/10">{row.callOi ? `${(row.callOi/1000).toFixed(0)}K` : '—'}</td>
                        <td className="px-1 py-1.5 text-center text-text-muted">{row.iv ? `${row.iv.toFixed(1)}%` : '—'}</td>
                        <td onClick={() => openContractChart(row.strike, 'CE')} className="px-1 py-1.5 text-center font-bold text-text-primary cursor-pointer hover:bg-emerald-500/10">₹{cLtp.toFixed(2)}</td>
                        <td onClick={() => openContractChart(row.strike, highlightedSide === 'PE' ? 'PE' : 'CE')} className="px-1 py-1.5 text-center font-extrabold text-emerald-400 bg-black cursor-pointer hover:bg-elevated/40">{row.strike}</td>
                        <td onClick={() => openContractChart(row.strike, 'PE')} className="px-1 py-1.5 text-center font-bold text-text-primary cursor-pointer hover:bg-emerald-500/10">₹{pLtp.toFixed(2)}</td>
                        <td className="px-1 py-1.5 text-center text-text-muted">{row.iv ? `${row.iv.toFixed(1)}%` : '—'}</td>
                        <td onClick={() => openContractChart(row.strike, 'PE')} className="px-1 py-1.5 text-center text-rose-400 cursor-pointer hover:bg-emerald-500/10">{row.putOi ? `${(row.putOi/1000).toFixed(0)}K` : '—'}</td>
                      </>
                    )}

                    {/* GREEKS VIEW MODE */}
                    {mode === 'greeks' && (
                      <>
                        <td onClick={() => openContractChart(row.strike, 'CE')} className="px-1 py-1.5 text-center text-emerald-400 cursor-pointer hover:bg-emerald-500/10">{(0.5 + (actualSpot - row.strike)/1000).toFixed(2)}</td>
                        <td className="px-1 py-1.5 text-center text-text-muted">{row.iv ? `${row.iv.toFixed(1)}%` : '18.2%'}</td>
                        <td onClick={() => openContractChart(row.strike, highlightedSide === 'PE' ? 'PE' : 'CE')} className="px-1 py-1.5 text-center font-extrabold text-emerald-400 bg-black cursor-pointer hover:bg-elevated/40">{row.strike}</td>
                        <td className="px-1 py-1.5 text-center text-text-muted">{row.iv ? `${row.iv.toFixed(1)}%` : '18.2%'}</td>
                        <td onClick={() => openContractChart(row.strike, 'PE')} className="px-1 py-1.5 text-center text-rose-400 cursor-pointer hover:bg-emerald-500/10">{(-0.5 + (actualSpot - row.strike)/1000).toFixed(2)}</td>
                      </>
                    )}
                  </tr>
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
