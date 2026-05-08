'use client';

import React from 'react';
import { Group, Panel, Separator, usePanelRef } from 'react-resizable-panels';
import { PanelRightClose, PanelRightOpen } from 'lucide-react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import { TradeProfile, useTradeStore } from '../../store/useTradeStore';

interface SwingLayoutProps { activeProfile?: TradeProfile; timeframe?: string; }
type TrendBias = 'BULLISH' | 'BEARISH' | 'NEUTRAL';
interface TimeframeTrend { timeframe: string; bias: TrendBias; strength: number; }

const TIMEFRAME_TRENDS: TimeframeTrend[] = [
  { timeframe: '1H', bias: 'BULLISH', strength: 72 },
  { timeframe: '4H', bias: 'NEUTRAL', strength: 50 },
  { timeframe: '1D', bias: 'BULLISH', strength: 84 },
  { timeframe: '1W', bias: 'BULLISH', strength: 91 },
];

function biasColor(b: TrendBias) { return b === 'BULLISH' ? 'text-bull' : b === 'BEARISH' ? 'text-bear' : 'text-neutral'; }
function biasBarColor(b: TrendBias) { return b === 'BULLISH' ? 'bg-bull' : b === 'BEARISH' ? 'bg-bear' : 'bg-neutral'; }
function sentimentColor(s: number) { return s >= 70 ? 'text-bull' : s >= 40 ? 'text-neutral' : 'text-bear'; }
function sentimentBarColor(s: number) { return s >= 70 ? 'bg-bull' : s >= 40 ? 'bg-neutral' : 'bg-bear'; }

function SwingConfluencePanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);
  const score = latestInsight?.sentiment_score ?? null;
  return (
    <div id="swing-confluence-panel" className="flex h-full flex-col rounded-lg border border-border-default bg-surface text-sm select-none overflow-hidden">
      <div className="flex shrink-0 items-center justify-between border-b border-border-default px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-text-primary tracking-wide">Confluence</span>
          <span className="rounded bg-emerald-500/10 px-1.5 py-px text-[9px] font-bold text-emerald-400 uppercase tracking-widest">Swing</span>
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
        <div className="px-4 pt-3 pb-1.5"><h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Multi-Timeframe Trend</h3></div>
        <div className="flex flex-col gap-2 px-4 pb-3">
          {TIMEFRAME_TRENDS.map((t) => (
            <div key={t.timeframe} className="flex flex-col gap-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-text-primary">{t.timeframe}</span>
                <span className={`text-xs font-bold ${biasColor(t.bias)}`}>{t.bias}</span>
              </div>
              <div className="h-1 w-full rounded-full bg-elevated"><div className={`h-1 rounded-full transition-all duration-300 ${biasBarColor(t.bias)}`} style={{ width: `${t.strength}%` }} /></div>
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-1 min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between px-4 pt-3 pb-1.5">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">AI News Sentiment</h3>
          <div className="flex items-center gap-1.5">
            {score !== null ? (<><span className={`text-sm font-bold tabular-nums ${sentimentColor(score)}`}>{score}</span><span className="text-[9px] text-text-muted font-medium">/ 100</span></>) : (<span className="text-[9px] text-text-muted font-medium italic">—</span>)}
          </div>
        </div>
        <div className="mx-4 mb-2">
          <div className="h-1.5 w-full rounded-full bg-elevated overflow-hidden">
            {score !== null ? (<div className={`h-1.5 rounded-full transition-all duration-500 ${sentimentBarColor(score)}`} style={{ width: `${score}%` }} />) : (<div className="h-1.5 w-0 rounded-full" />)}
          </div>
          <div className="flex justify-between mt-0.5 text-[8px] text-text-muted"><span>Fear</span><span>Greed</span></div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          {latestInsight ? (
            <div className="flex flex-col gap-2.5">
              <div className="flex gap-2 rounded-md border border-border-subtle bg-elevated/50 p-2.5 transition-colors hover:bg-elevated">
                <div className="mt-1 shrink-0"><span className={`inline-flex h-2 w-2 rounded-full ${latestInsight.sentiment_score >= 60 ? 'bg-bull' : latestInsight.sentiment_score >= 40 ? 'bg-neutral' : 'bg-bear'}`} /></div>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-medium text-text-primary leading-snug line-clamp-2">{latestInsight.headline}</p>
                  <div className="mt-1 flex items-center gap-2 text-[9px] text-text-muted"><span className="font-medium">DeepSeek AI</span><span>·</span><span>{latestInsight.symbol}</span><span>·</span><span>{latestInsight.anomaly_pct.toFixed(1)}% anomaly</span></div>
                </div>
              </div>
              <div className="rounded-md border border-border-subtle bg-elevated/50 p-2.5"><p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">{latestInsight.analysis_text}</p></div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center"><div className="flex flex-col items-center gap-2 text-center"><div className="flex h-8 w-8 items-center justify-center rounded-full bg-elevated"><span className="text-sm">🧠</span></div><p className="text-[11px] text-text-muted leading-snug">Awaiting Market Anomalies...</p><p className="text-[9px] text-text-muted/60">Insights appear when a ≥2% price swing is detected</p></div></div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function SwingLayout({ activeProfile = 'SWING', timeframe = '1h' }: SwingLayoutProps) {
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
    <div id="swing-hud" className="h-full p-3">
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
            <div className="flex-1 min-h-0"><SwingConfluencePanel /></div>
          </div>
        </Panel>
      </Group>
    </div>
  );
}
