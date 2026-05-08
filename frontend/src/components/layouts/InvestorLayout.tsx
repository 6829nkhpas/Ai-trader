'use client';

import React from 'react';
import { Group, Panel, Separator, usePanelRef } from 'react-resizable-panels';
import { PanelRightClose, PanelRightOpen } from 'lucide-react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import { TradeProfile, useTradeStore } from '../../store/useTradeStore';

interface InvestorLayoutProps { activeProfile?: TradeProfile; timeframe?: string; }
interface MacroIndicator { label: string; value: string; change?: string; direction?: 'up' | 'down' | 'flat'; }

const MACRO_INDICATORS: MacroIndicator[] = [
  { label: 'Fed Funds Rate', value: '5.25%', change: '+25 bps', direction: 'up' },
  { label: 'Core CPI (YoY)', value: '3.8%', change: '-0.2%', direction: 'down' },
  { label: '10Y Treasury', value: '4.31%', change: '+5 bps', direction: 'up' },
  { label: 'US Dollar Index', value: '104.52', change: '-0.3%', direction: 'down' },
  { label: 'VIX', value: '14.82', change: '-1.2', direction: 'down' },
  { label: 'GDP Growth (Q1)', value: '2.1%', change: '+0.3%', direction: 'up' },
];
const PORTFOLIO_METRICS = [
  { label: 'Sharpe Ratio', value: '1.42' },
  { label: 'Max Drawdown', value: '-8.3%' },
  { label: 'Beta', value: '1.08' },
  { label: 'Alpha (ann.)', value: '+3.2%' },
];

function dirIcon(d?: 'up' | 'down' | 'flat') { return d === 'up' ? '▲' : d === 'down' ? '▼' : '—'; }
function dirColor(d?: 'up' | 'down' | 'flat') { return d === 'up' ? 'text-bull' : d === 'down' ? 'text-bear' : 'text-text-muted'; }

function MacroSentimentPanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);
  return (
    <div id="macro-sentiment-panel" className="flex h-full flex-col rounded-lg border border-border-default bg-surface text-sm select-none overflow-hidden">
      <div className="flex shrink-0 items-center justify-between border-b border-border-default px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-text-primary tracking-wide">Macro Intelligence</span>
          <span className="rounded bg-emerald-500/10 px-1.5 py-px text-[9px] font-bold text-emerald-400 uppercase tracking-widest">Investor</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-1.5 w-1.5">
            <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-50 ${latestInsight ? 'bg-emerald-400' : 'bg-amber-400'}`} />
            <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${latestInsight ? 'bg-emerald-500' : 'bg-amber-500'}`} />
          </span>
          <span className="text-[9px] font-medium text-text-muted uppercase tracking-widest">{latestInsight ? 'Live' : 'Awaiting'}</span>
        </div>
      </div>
      <div className="flex flex-col border-b border-border-default">
        <div className="px-4 pt-3 pb-1.5"><h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Macro Indicators</h3></div>
        <div className="flex flex-col gap-0 px-4 pb-3">
          {MACRO_INDICATORS.map((ind) => (
            <div key={ind.label} className="flex items-center justify-between py-1.5 border-b border-border-subtle last:border-0">
              <span className="text-[11px] text-text-secondary">{ind.label}</span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-semibold text-text-primary tabular-nums">{ind.value}</span>
                {ind.change && <span className={`text-[10px] font-medium tabular-nums ${dirColor(ind.direction)}`}>{dirIcon(ind.direction)} {ind.change}</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-col border-b border-border-default">
        <div className="px-4 pt-3 pb-1.5"><h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Portfolio Risk Metrics</h3></div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 px-4 pb-3">
          {PORTFOLIO_METRICS.map((m) => (<div key={m.label} className="flex items-center justify-between"><span className="text-[10px] text-text-muted">{m.label}</span><span className="text-[11px] font-semibold text-text-primary tabular-nums">{m.value}</span></div>))}
        </div>
      </div>
      <div className="flex flex-1 min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between px-4 pt-3 pb-1.5">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Quant-RAG Outlook</h3>
          <span className={`rounded px-1.5 py-px text-[9px] font-bold uppercase tracking-widest ${latestInsight ? 'bg-cyan-500/10 text-cyan-600' : 'bg-amber-500/10 text-amber-500'}`}>{latestInsight ? 'AI Generated' : 'Standby'}</span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          {latestInsight ? (
            <div className="flex flex-col gap-2.5">
              <div className="rounded-md border border-border-subtle bg-elevated/50 p-3">
                <div className="flex items-start gap-2">
                  <span className={`mt-0.5 inline-flex h-2 w-2 shrink-0 rounded-full ${latestInsight.sentiment_score >= 60 ? 'bg-bull' : latestInsight.sentiment_score >= 40 ? 'bg-neutral' : 'bg-bear'}`} />
                  <div>
                    <p className="text-[12px] font-semibold text-text-primary leading-snug">{latestInsight.headline}</p>
                    <div className="mt-1 flex items-center gap-2 text-[9px] text-text-muted"><span className="font-medium">{latestInsight.symbol}</span><span>·</span><span>{latestInsight.anomaly_pct.toFixed(1)}% anomaly</span><span>·</span><span>Sentiment: {latestInsight.sentiment_score}/100</span></div>
                  </div>
                </div>
              </div>
              <div className="rounded-md border border-border-subtle bg-elevated/50 p-3"><p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">{latestInsight.analysis_text}</p></div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center"><div className="flex flex-col items-center gap-2 text-center"><div className="flex h-8 w-8 items-center justify-center rounded-full bg-elevated"><span className="text-sm">🧠</span></div><p className="text-[11px] text-text-muted leading-snug">Awaiting Market Anomalies...</p><p className="text-[9px] text-text-muted/60">AI outlook appears when a ≥2% price swing is detected</p></div></div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function InvestorLayout({ activeProfile = 'INVESTOR', timeframe = '1D' }: InvestorLayoutProps) {
  const sidebarRef = usePanelRef();
  const [isCollapsed, setIsCollapsed] = React.useState(false);

  const handleToggle = () => {
    const p = sidebarRef.current;
    if (!p) return;
    if (p.isCollapsed()) { p.expand(); setIsCollapsed(false); }
    else { p.collapse(); setIsCollapsed(true); }
  };

  const handleResize = () => {
    const p = sidebarRef.current;
    if (p) setIsCollapsed(p.isCollapsed());
  };

  return (
    <div id="investor-hud" className="h-full p-3">
      <Group orientation="horizontal">
        <Panel defaultSize={75} minSize={40}>
          <div className="flex h-full flex-col min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
            <AlphaPredictiveChart activeProfile={activeProfile} timeframe={timeframe as Timeframe} isExpanded={isCollapsed} onToggleExpand={handleToggle} />
          </div>
        </Panel>
        <Separator className="group flex w-2 items-center justify-center cursor-col-resize">
          <div className="h-full w-[3px] rounded-full bg-slate-800 transition-colors duration-150 group-hover:bg-slate-600 group-active:bg-emerald-500/60" />
        </Separator>
        <Panel panelRef={sidebarRef} defaultSize={25} minSize={15} collapsible collapsedSize={0} onResize={handleResize}>
          <div className="flex h-full flex-col min-h-0">
            <div className="flex shrink-0 items-center justify-end px-2 py-1">
              <button type="button" onClick={handleToggle} className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary" title={isCollapsed ? 'Expand' : 'Collapse'}>
                {isCollapsed ? <PanelRightOpen size={14} /> : <PanelRightClose size={14} />}
              </button>
            </div>
            <div className="flex-1 min-h-0"><MacroSentimentPanel /></div>
          </div>
        </Panel>
      </Group>
    </div>
  );
}
