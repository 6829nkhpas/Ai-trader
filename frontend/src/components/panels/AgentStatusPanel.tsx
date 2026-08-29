'use client';

/**
 * AI Swarm Status.
 *
 * Every row except the aggregator used to be a hardcoded `status: 'LIVE'`, so
 * the panel claimed the sentiment agent was live while /api/sentiment was
 * returning 503 and the candle feed was down. A status board that cannot report
 * a fault is worse than no status board — it is the "mock data" in this panel.
 *
 * Each row is now derived from an observable signal this store already tracks:
 *   · Ingestion Engine     — the Alpha OHLC candle feed (are bars arriving?)
 *   · Technical Agent      — the decision feed's technical weight contribution
 *   · NLP Sentiment Agent  — the /api/sentiment fetch state in the quant store
 *   · Aggregator           — the decision WebSocket connection state
 *
 * Nothing here probes a service directly; these are the same inputs the panels
 * render from, which is exactly what makes the status honest: if a row says LIVE,
 * data really did arrive.
 */

import React from 'react';
import { Activity, Brain, Cpu, MessageSquare } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';
import { useQuantStore } from '../../store/useQuantStore';

/** How recently a feed must have produced data to count as live. */
const FRESH_WINDOW_MS = 2 * 60 * 1000;

type AgentStatus = 'LIVE' | 'CONNECTED' | 'CONNECTING' | 'IDLE' | 'DEGRADED' | 'OFFLINE';

function statusColor(status: AgentStatus): string {
  if (status === 'LIVE' || status === 'CONNECTED') return 'text-status-live';
  if (status === 'CONNECTING' || status === 'IDLE') return 'text-status-warning';
  return 'text-status-error';
}

function statusDot(status: AgentStatus): string {
  if (status === 'LIVE' || status === 'CONNECTED') return 'bg-status-live';
  if (status === 'CONNECTING' || status === 'IDLE') return 'bg-status-warning';
  return 'bg-status-error';
}

export default function AgentStatusPanel() {
  const connectionStatus = useTradeStore((s) => s.connectionStatus);
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);

  const activeSentiment = useQuantStore((s) => s.activeSentiment);
  const isFetchingSentiment = useQuantStore((s) => s.isFetchingSentiment);
  const sentimentError = useQuantStore((s) => s.sentimentError);

  // A ticking clock, so freshness is re-evaluated as time passes rather than
  // only when a new candle happens to arrive. Without this a feed that went
  // silent would keep reporting LIVE indefinitely — the exact dishonesty this
  // panel is being fixed for. Kept out of the memo below because reading the
  // clock inside a memo is an impure render.
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  // ── Ingestion: has a candle arrived recently? ───────────────────────
  const ingestionStatus: AgentStatus = React.useMemo(() => {
    if (ohlcCandles.length === 0) return 'OFFLINE';
    let newest = 0;
    for (const c of ohlcCandles) {
      if (c.start_timestamp_ms > newest) newest = c.start_timestamp_ms;
    }
    // Outside market hours no new bars form, so a stale-but-present feed is
    // IDLE, not a fault. Only a total absence of bars is OFFLINE.
    return now - newest < FRESH_WINDOW_MS ? 'LIVE' : 'IDLE';
  }, [ohlcCandles, now]);

  // ── Technical agent: is the fusion engine producing a technical weight? ──
  const technicalStatus: AgentStatus = React.useMemo(() => {
    const latest = liveDecisions[liveDecisions.length - 1];
    if (!latest) return 'OFFLINE';
    // A decision that arrived with a zero technical weight means the aggregator
    // ran without a technical contribution — degraded, not healthy.
    return (latest.technical_weight_used ?? 0) > 0 ? 'LIVE' : 'DEGRADED';
  }, [liveDecisions]);

  // ── Sentiment agent: the actual /api/sentiment outcome ──────────────
  const sentimentStatus: AgentStatus = isFetchingSentiment
    ? 'CONNECTING'
    : sentimentError
      ? 'OFFLINE'
      : activeSentiment
        ? 'LIVE'
        : 'IDLE';

  const aggregatorStatus: AgentStatus =
    connectionStatus === 'CONNECTED'
      ? 'CONNECTED'
      : connectionStatus === 'CONNECTING'
        ? 'CONNECTING'
        : 'OFFLINE';

  const agents: { name: string; icon: typeof Activity; status: AgentStatus; title: string }[] = [
    {
      name: 'Ingestion Engine',
      icon: Activity,
      status: ingestionStatus,
      title: 'Live candle feed (Alpha OHLC WebSocket)',
    },
    {
      name: 'Technical Agent',
      icon: Cpu,
      status: technicalStatus,
      title: 'Technical contribution to the latest fused decision',
    },
    {
      name: 'NLP Sentiment Agent',
      icon: MessageSquare,
      status: sentimentStatus,
      title: sentimentError ?? 'News sentiment classification service',
    },
    {
      name: 'Aggregator',
      icon: Brain,
      status: aggregatorStatus,
      title: 'Decision WebSocket',
    },
  ];

  return (
    <section className="rounded-lg border border-border-default bg-card p-4 panel-shadow">
      <div className="text-xs font-semibold uppercase tracking-widest text-text-secondary">AI Swarm Status</div>
      <div className="mt-3 flex flex-col gap-2">
        {agents.map((agent) => (
          <div key={agent.name} className="flex items-center justify-between" title={agent.title}>
            <div className="flex items-center gap-2">
              <agent.icon size={14} className={statusColor(agent.status)} />
              <span className="text-xs font-medium text-text-primary">{agent.name}</span>
            </div>
            <div className="flex items-center gap-1.5 rounded-full border border-border-default bg-surface px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-text-secondary">
              <span className={`h-1.5 w-1.5 rounded-full ${statusDot(agent.status)}`} />
              <span>{agent.status}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
