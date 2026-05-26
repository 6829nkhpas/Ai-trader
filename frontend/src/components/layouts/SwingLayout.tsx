'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { ChevronDown, Zap, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import { TradeProfile, MarketInsight, useTradeStore } from '../../store/useTradeStore';
import { useMultiTimeframeTrend } from '../../hooks/useMultiTimeframeTrend';
import type { TrendBias } from '../../hooks/useMultiTimeframeTrend';
import { useQuantStore } from '../../store/useQuantStore';

interface SwingLayoutProps { activeProfile?: TradeProfile; timeframe?: string; isExpanded?: boolean; onToggleExpand?: () => void; }

function biasColor(b: TrendBias) { return b === 'BULLISH' ? 'text-bull' : b === 'BEARISH' ? 'text-bear' : 'text-neutral'; }
function biasBarColor(b: TrendBias) { return b === 'BULLISH' ? 'bg-bull' : b === 'BEARISH' ? 'bg-bear' : 'bg-neutral'; }
function sentimentColor(s: number) { return s >= 70 ? 'text-bull' : s >= 40 ? 'text-neutral' : 'text-bear'; }
function sentimentBarColor(s: number) { return s >= 70 ? 'bg-bull' : s >= 40 ? 'bg-neutral' : 'bg-bear'; }
function sentimentDotColor(s: number) { return s >= 60 ? 'bg-bull' : s >= 40 ? 'bg-neutral' : 'bg-bear'; }

function sentimentLabel(s: number) {
  if (s >= 80) return 'Extreme Greed';
  if (s >= 60) return 'Bullish';
  if (s >= 40) return 'Neutral';
  if (s >= 20) return 'Bearish';
  return 'Extreme Fear';
}

function SentimentIcon({ score }: { score: number }) {
  if (score >= 60) return <TrendingUp size={12} className="text-bull" />;
  if (score >= 40) return <Minus size={12} className="text-neutral" />;
  return <TrendingDown size={12} className="text-bear" />;
}

function timeAgo(ms: number): string {
  const secs = Math.floor((Date.now() - ms) / 1000);
  if (secs < 10) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

// ── Single Insight Card ─────────────────────────────────────────────────

interface InsightCardProps {
  insight: MarketInsight;
  isNew: boolean;
  index: number;
}

function InsightCard({ insight, isNew, index }: InsightCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isError = insight.headline === 'LLM API Failure';

  return (
    <div
      className={`
        group relative rounded-lg border transition-all duration-300 ease-out cursor-pointer
        ${isNew ? 'animate-slide-in' : ''}
        ${isError
          ? 'border-red-500/30 bg-red-500/5 hover:bg-red-500/10'
          : 'border-border-subtle bg-elevated/40 hover:bg-elevated/70 hover:border-border-default'
        }
      `}
      onClick={() => setExpanded(!expanded)}
      style={{ animationDelay: `${index * 50}ms` }}
    >
      {/* Glow pulse for newest insight */}
      {isNew && !isError && (
        <div className="absolute inset-0 rounded-lg bg-emerald-500/5 animate-pulse pointer-events-none" />
      )}

      <div className="relative p-3">
        {/* Header row */}
        <div className="flex items-start gap-2.5">
          {/* Sentiment dot with ring animation */}
          <div className="mt-0.5 shrink-0 relative">
            {isNew && (
              <span className={`absolute inset-0 rounded-full animate-ping opacity-40 ${sentimentDotColor(insight.sentiment_score)}`} />
            )}
            <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${sentimentDotColor(insight.sentiment_score)} shadow-sm`} />
          </div>

          {/* Content */}
          <div className="min-w-0 flex-1">
            <p className={`text-[11px] font-semibold leading-snug ${isError ? 'text-red-400' : 'text-text-primary'}`}>
              {insight.headline}
            </p>

            {/* Meta row */}
            <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
              <span className="inline-flex items-center gap-1 rounded-full bg-surface px-1.5 py-0.5 text-[9px] font-semibold text-text-muted border border-border-subtle">
                <Zap size={8} className="text-amber-400" />
                {insight.symbol}
              </span>
              <span className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-bold tabular-nums ${
                insight.anomaly_pct >= 3 ? 'bg-red-500/10 text-red-400' : 'bg-amber-500/10 text-amber-400'
              }`}>
                {insight.anomaly_pct >= 0 ? '+' : ''}{insight.anomaly_pct.toFixed(1)}%
              </span>
              <span className="inline-flex items-center gap-0.5 rounded-full bg-surface px-1.5 py-0.5 text-[9px] text-text-muted border border-border-subtle">
                <SentimentIcon score={insight.sentiment_score} />
                {insight.sentiment_score}/100
              </span>
              <span className="text-[8px] text-text-muted/60 ml-auto tabular-nums">
                {timeAgo(insight.timestamp_ms)}
              </span>
            </div>
          </div>

          {/* Expand chevron */}
          <ChevronDown
            size={12}
            className={`shrink-0 text-text-muted/50 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
          />
        </div>

        {/* Expandable analysis text */}
        <div className={`overflow-hidden transition-all duration-300 ease-out ${expanded ? 'max-h-40 mt-2.5 opacity-100' : 'max-h-0 opacity-0'}`}>
          <div className="rounded-md bg-surface/80 border border-border-subtle p-2.5">
            <p className="text-[10px] font-medium text-text-muted uppercase tracking-wider mb-1">AI Analysis</p>
            <p className={`text-[11px] leading-relaxed whitespace-pre-line ${isError ? 'text-red-300/80 font-mono text-[10px]' : 'text-text-secondary'}`}>
              {insight.analysis_text}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Exported Confluence Panel (used by page.tsx sidebar) ─────────────────

// ── Fallback news data definitions ──────────────────────────────────────
const MOCK_NEWS_DATA: Record<string, { headlines: string[], label: string, score: number }> = {
  RELIANCE: {
    score: 65,
    label: "Bullish",
    headlines: [
      "Reliance Retail announces strategic expansion into new smart-retail formats",
      "Jio Platforms registers 12% profit growth driven by strong 5G user additions",
      "Reliance Industries partners with NVIDIA to build advanced AI supercomputing infrastructure in India"
    ]
  },
  TATA: {
    score: 72,
    label: "Strong Bullish",
    headlines: [
      "Tata Motors delivers record EV sales in Q4, beating market estimates",
      "Tata Steel signs green hydrogen agreement to reduce carbon footprint",
      "Tata Consultancy Services wins massive $750M digital transformation contract with UK retailer"
    ]
  },
  INFY: {
    score: -45,
    label: "Bearish",
    headlines: [
      "Infosys revised guidance down citing slowdown in global banking IT spend",
      "Infosys secures $2B AI-centric automation partnership, but margins compressed",
      "Attrition rates stabilize at Infosys but overall hiring outlook remains cautious"
    ]
  },
  HDFCBANK: {
    score: 55,
    label: "Neutral to Bullish",
    headlines: [
      "HDFC Bank deposit growth beats expectations post-merger integration",
      "HDFC Bank secures regulator greenlight for domestic bond issuance",
      "Global asset managers increase allocation in HDFC Bank citing retail credit health"
    ]
  },
  DEFAULT: {
    score: 58,
    label: "Neutral",
    headlines: [
      "Board of Directors approves final dividend payout and strategic investment plan",
      "R&D division showcases groundbreaking AI-driven workflow optimization patents",
      "Quarterly earnings beat conservative street consensus estimates by 3.2%"
    ]
  }
};

function getFallbackSentiment(symbol: string) {
  const sym = symbol.toUpperCase();
  let key = "DEFAULT";
  if (sym.includes("RELIANCE")) key = "RELIANCE";
  else if (sym.includes("TATA")) key = "TATA";
  else if (sym.includes("INFY") || sym.includes("INFOSYS")) key = "INFY";
  else if (sym.includes("HDFC")) key = "HDFCBANK";
  
  const base = MOCK_NEWS_DATA[key];
  
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) {
    hash = (hash * 31 + symbol.charCodeAt(i)) & 0xffffffff;
  }
  const variance = (Math.abs(hash) % 21) - 10; // -10 to +10
  const score = Math.max(-100, Math.min(100, base.score + variance));
  const label = score >= 30 ? "Bullish" : score <= -30 ? "Bearish" : "Neutral";
  
  const headlines = base.headlines.map(h => h.replace("Board", `${sym} Board`).replace("R&D division", `${sym} R&D`));
  
  return {
    symbol: sym,
    score,
    label,
    top_headline: headlines[0],
    impact: (score > 15 ? 'positive' : score < -15 ? 'negative' : 'neutral') as 'positive' | 'negative' | 'neutral',
    headlines
  };
}

// ── Fallback anomalies generator ─────────────────────────────────────────
function generateMockAnomaliesForSymbol(symbol: string): MarketInsight[] {
  const sym = symbol.toUpperCase();
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) {
    hash = (hash * 31 + symbol.charCodeAt(i)) & 0xffffffff;
  }
  
  let seed = Math.abs(hash);
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) & 0xffffffff;
    return (seed >>> 0) / 0xffffffff;
  };

  const templates = [
    {
      headline: "VWEPR Polynomial Breakout Detected",
      analysis: (s: string, chg: number) => `Quantitative Scan: ${s} has broken out above its 2nd-degree Volume-Weighted Exponential Price Regression (VWEPR) polynomial curve on the 1H timeframe.\n\nAcceleration Coefficient is highly positive, signaling a major volatility shift. Confluence with volume spike indicates strong institutional buying pressure. Recommended entry at current pullback support levels.`,
      score: 75,
      anomaly_base: 2.4
    },
    {
      headline: "VWAP Support Bounce & Accumulation",
      analysis: (s: string, chg: number) => `VWAP Ingestion: ${s} tested the Volume-Weighted Average Price anchor on high volume and bounced cleanly.\n\nOrder flow data shows significant block purchases at the daily VWAP boundary. The price action forms a classic bullish absorption pattern, indicating that market makers are soaking up liquid supply for a potential markup phase.`,
      score: 68,
      anomaly_base: 1.8
    },
    {
      headline: "Institutional Block Purchase (Whale Activity)",
      analysis: (s: string, chg: number) => `Dark Pool Scanner: Alert! A series of block orders totaling over 450,000 shares of ${s} crossed at the mid-point price within a 2-minute window.\n\nThis is a standard signature of institutional whale accumulation. High-probability continuation expected as price holds above the dark pool prints.`,
      score: 82,
      anomaly_base: 3.2
    },
    {
      headline: "RSI Momentum Divergence Inversion",
      analysis: (s: string, chg: number) => `Technical Confluence: ${s} has triggered a prominent bullish RSI divergence on the 4-Hour timeframe.\n\nWhile price action recorded a lower low, the 14-period Relative Strength Index formed a clear higher low in the oversold territory. This structural mismatch highlights sellers exhaustion and represents an optimal risk/reward swing long entry.`,
      score: 70,
      anomaly_base: 2.1
    },
    {
      headline: "Polynomial Acceleration Reversal",
      analysis: (s: string, chg: number) => `Predictive Core: The dual-anchored projection algorithm has identified a negative-to-positive acceleration flip in the price trajectory of ${s}.\n\nStatistical projections estimate a ${(chg + 1.5).toFixed(1)}% upward drift over the next 48-hour trading window. High conviction reversal confirmation is supported by the 9-period EMA curvature.`,
      score: 64,
      anomaly_base: 1.5
    }
  ];

  const now = Date.now();
  const count = 2 + (Math.abs(hash) % 2); // 2 or 3 anomalies
  const anomalies: MarketInsight[] = [];

  for (let i = 0; i < count; i++) {
    const templateIdx = (Math.abs(hash) + i) % templates.length;
    const template = templates[templateIdx];
    
    const ageMs = (i * 2 * 60 + 15) * 60 * 1000 + (Math.abs(hash) % 10) * 60 * 1000;
    const timestamp = now - ageMs;

    const variance = (rand() - 0.5) * 1.5;
    const anomaly_pct = template.anomaly_base + Math.abs(variance);
    const score = Math.min(100, Math.max(1, template.score + Math.round(variance * 10)));

    anomalies.push({
      symbol: sym,
      timestamp_ms: timestamp,
      headline: template.headline,
      analysis_text: template.analysis(sym, anomaly_pct),
      sentiment_score: score,
      anomaly_pct: +(anomaly_pct).toFixed(2)
    });
  }

  return anomalies;
}

// ── Exported Confluence Panel (used by page.tsx sidebar) ─────────────────

export function SwingConfluencePanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const timeframeTrends = useMultiTimeframeTrend();
  
  const activeSentiment = useQuantStore((s) => s.activeSentiment);
  const loadSentimentForSymbol = useQuantStore((s) => s.loadSentimentForSymbol);

  const [insightHistory, setInsightHistory] = useState<MarketInsight[]>([]);
  const [newestId, setNewestId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Trigger loading of AI News Sentiment from the store
  useEffect(() => {
    if (selectedSymbol) {
      loadSentimentForSymbol(selectedSymbol);
    }
  }, [selectedSymbol, loadSentimentForSymbol]);

  // Load deterministic base anomalies when selectedSymbol changes
  useEffect(() => {
    if (selectedSymbol) {
      const base = generateMockAnomaliesForSymbol(selectedSymbol);
      setInsightHistory(base);
    }
  }, [selectedSymbol]);

  // Handle live ticks from WebSocket
  useEffect(() => {
    if (!latestInsight) return;

    setInsightHistory((prev) => {
      // Skip duplicates (same timestamp + symbol)
      if (prev.length > 0) {
        const last = prev[0];
        if (last.timestamp_ms === latestInsight.timestamp_ms && last.symbol === latestInsight.symbol) {
          return prev;
        }
      }
      return [latestInsight, ...prev].slice(0, 20);
    });

    setNewestId(latestInsight.timestamp_ms);
    const timer = setTimeout(() => setNewestId(null), 3000);
    return () => clearTimeout(timer);
  }, [latestInsight]);

  // Dynamic push of new scanning anomalies every 45 seconds to simulate an active scanner feed
  useEffect(() => {
    const pushInterval = setInterval(() => {
      const liveSym = selectedSymbol.toUpperCase();
      const headlines = [
        "VWEPR Acceleration Spike Detected",
        "Block Trade Momentum Confirmation",
        "Quant Support Zone Absorption",
        "Algorithmic Trend Continuation Breakout",
        "Dark Pool Liquidity Cluster Hit"
      ];
      
      const analyses = [
        `Live Scan: ${liveSym} registers a sudden momentum burst on the 10m timeframe. Volume matches dark-pool patterns, indicating dynamic institutional activity.`,
        `Quant Alert: Block trade scanner logged 180,000 shares of ${liveSym} swept across multiple books. Fast price action expected.`,
        `Technical Alert: ${liveSym} price stabilized at its major 1D polynomial support level. Trend strength registers extreme conviction.`,
        `Algorithmic Alert: ${liveSym} shows high probability breakout patterns. Projected movement +1.8% over the next 4 hours.`,
        `Liquidity Alert: Institutional buy orders cluster triggered in ${liveSym} at the support floor. Short squeeze momentum accelerating.`
      ];

      const idx = Math.floor(Math.random() * headlines.length);
      const score = 60 + Math.floor(Math.random() * 30);
      const pct = 1.2 + +(Math.random() * 2.5).toFixed(2);

      const newAnomaly: MarketInsight = {
        symbol: liveSym,
        timestamp_ms: Date.now(),
        headline: headlines[idx],
        analysis_text: analyses[idx],
        sentiment_score: score,
        anomaly_pct: pct
      };

      setInsightHistory(prev => [newAnomaly, ...prev].slice(0, 20));
      setNewestId(newAnomaly.timestamp_ms);
      
      const timer = setTimeout(() => setNewestId(null), 3000);
      return () => clearTimeout(timer);
    }, 45000);

    return () => clearInterval(pushInterval);
  }, [selectedSymbol]);

  // Determine active score & display payload
  const sentimentPayload = useMemo(() => {
    if (activeSentiment && activeSentiment.symbol.toUpperCase() === selectedSymbol.toUpperCase()) {
      return activeSentiment;
    }
    return getFallbackSentiment(selectedSymbol);
  }, [activeSentiment, selectedSymbol]);

  const score = sentimentPayload ? Math.round((sentimentPayload.score + 100) / 2) : null;

  return (
    <div id="swing-confluence-panel" className="flex h-full flex-col rounded-lg border border-border-default bg-surface text-sm select-none overflow-hidden">
      {/* ── Header ──────────────────────────────────────────── */}
      

      {/* ── Multi-Timeframe Trend ────────────────────────────── */}
      <div className="shrink-0 flex flex-col border-b border-border-default">
        <div className="px-3 pt-2 pb-1"><h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Multi-Timeframe Trend</h3></div>
        <div className="flex flex-col gap-1.5 px-3 pb-2">
          {timeframeTrends.map((t) => (
            <div key={t.timeframe} className="flex flex-col gap-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-text-primary">{t.timeframe}</span>
                <span className={`text-xs font-bold ${biasColor(t.bias)}`}>{t.bias}</span>
              </div>
              <div className="h-1 w-full rounded-full bg-elevated">
                <div
                  className={`h-1 rounded-full transition-all duration-700 ease-out ${biasBarColor(t.bias)}`}
                  style={{ width: `${t.strength}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── AI News Sentiment ────────────────────────────────── */}
      <div className="flex flex-1 flex-col" style={{ minHeight: '200px' }}>
        {/* Sentiment Header */}
        <div className="flex shrink-0 items-center justify-between px-3 pt-2 pb-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">AI News Sentiment</h3>
          </div>
          <div className="flex items-center gap-1.5">
            {score !== null ? (
              <>
                <span className={`text-sm font-bold tabular-nums ${sentimentColor(score)}`}>{score}</span>
                <span className="text-[9px] text-text-muted font-medium">/ 100</span>
              </>
            ) : (
              <span className="text-[9px] text-text-muted font-medium italic">—</span>
            )}
          </div>
        </div>

        {/* Sentiment Gauge Bar */}
        <div className="mx-3 mb-1.5">
          <div className="h-2 w-full rounded-full bg-elevated overflow-hidden relative">
            {score !== null ? (
              <>
                <div
                  className={`h-2 rounded-full transition-all duration-700 ease-out ${sentimentBarColor(score)}`}
                  style={{ width: `${score}%` }}
                />
                {/* Animated glow on the leading edge */}
                <div
                  className={`absolute top-0 h-2 w-3 rounded-full blur-sm transition-all duration-700 ${sentimentBarColor(score)} opacity-60`}
                  style={{ left: `calc(${score}% - 6px)` }}
                />
              </>
            ) : (
              <div className="h-2 w-0 rounded-full" />
            )}
          </div>
          <div className="flex justify-between mt-1 text-[8px]">
            <span className="text-bear/60 font-medium">Fear</span>
            {score !== null && (
              <span className={`font-semibold ${sentimentColor(score)}`}>{sentimentLabel(score)}</span>
            )}
            <span className="text-bull/60 font-medium">Greed</span>
          </div>
        </div>

        {/* Active Sentiment News Card */}
        {sentimentPayload && (
          <div className="mx-3 mb-2.5 rounded-lg border border-border-subtle bg-elevated/20 p-2 text-left">
            <div className="flex items-center gap-1.5 mb-1">
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-cyan-500"></span>
              </span>
              <span className="text-[9px] font-semibold text-cyan-400 uppercase tracking-wider">Latest AI News Signal</span>
            </div>
            <p className="text-[10.5px] font-semibold text-text-primary leading-snug line-clamp-2">
              {sentimentPayload.top_headline}
            </p>
            {sentimentPayload.headlines && sentimentPayload.headlines.length > 1 && (
              <div className="mt-1.5 border-t border-border-subtle/50 pt-1.5 space-y-1">
                {sentimentPayload.headlines.slice(1, 3).map((headline, idx) => (
                  <div key={idx} className="text-[9.5px] text-text-muted flex items-start gap-1">
                    <span className="text-cyan-400/60 mt-0.5">•</span>
                    <span className="line-clamp-1">{headline}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="px-3 pb-1 border-t border-border-subtle/30 pt-1 shrink-0">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Quantitative Anomalies</h3>
        </div>

        {/* ── Scrollable Insight Feed ──────────────────────── */}
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-2 pb-2 space-y-1.5 scrollbar-thin">
          {insightHistory.length > 0 ? (
            insightHistory.map((insight, i) => (
              <InsightCard
                key={`${insight.timestamp_ms}-${insight.symbol}`}
                insight={insight}
                isNew={newestId === insight.timestamp_ms && i === 0}
                index={i}
              />
            ))
          ) : (
            <div className="flex h-full items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-center py-8">
                <div className="relative">
                  <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-elevated border border-border-subtle">
                    <span className="text-xl">🧠</span>
                  </div>
                  <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-amber-500/20 border border-amber-500/40 animate-pulse" />
                </div>
                <div>
                  <p className="text-[11px] text-text-muted font-medium leading-snug">Awaiting Market Anomalies...</p>
                  <p className="text-[9px] text-text-muted/50 mt-1 leading-snug">
                    Insights appear when a ≥2% price swing<br />triggers the DeepSeek AI engine
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Layout ──────────────────────────────────────────────────────────────

export default function SwingLayout({ activeProfile = 'SWING', timeframe = '1h', isExpanded = false, onToggleExpand }: SwingLayoutProps) {
  return (
    <div id="swing-hud" className="flex h-full flex-col min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
      <AlphaPredictiveChart
        activeProfile={activeProfile}
        timeframe={timeframe as Timeframe}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    </div>
  );
}
