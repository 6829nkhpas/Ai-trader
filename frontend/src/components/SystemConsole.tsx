'use client';

import React, { useState, useRef, useEffect } from 'react';
import { ChevronUp, ChevronDown, Terminal, Wifi, Radio, Brain, Gauge } from 'lucide-react';
import { useTradeStore } from '../store/useTradeStore';

// ── Types ──────────────────────────────────────────────────────────────

interface SystemLog {
  timestamp: number;
  level: 'INFO' | 'WARN' | 'ERROR';
  message: string;
}

// ── Status Indicator ───────────────────────────────────────────────────

function StatusDot({ status }: { status: 'connected' | 'connecting' | 'disconnected' | 'error' | 'standby' }) {
  const colorMap = {
    connected: 'bg-emerald-500',
    connecting: 'bg-amber-500 animate-pulse',
    disconnected: 'bg-red-500',
    error: 'bg-red-500',
    standby: 'bg-cyan-500',
  };

  return <span className={`inline-block h-2 w-2 rounded-full ${colorMap[status]}`} />;
}

function StatusLabel({ status }: { status: string }) {
  const labelMap: Record<string, string> = {
    connected: 'Connected',
    connecting: 'Connecting',
    disconnected: 'Disconnected',
    error: 'Error',
    standby: 'Standby',
  };
  return <span className="text-[10px] font-semibold uppercase tracking-wide">{labelMap[status] ?? status}</span>;
}

// ── Console Component ──────────────────────────────────────────────────

export default function SystemConsole() {
  const [isExpanded, setIsExpanded] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  // ── Read system state from store ─────────────────────────────────────
  const wsStatus = useTradeStore((s) => s.wsStatus);
  const connectionStatus = useTradeStore((s) => s.connectionStatus);
  const latencyMs = useTradeStore((s) => s.latencyMs);
  const latestInsight = useTradeStore((s) => s.latestInsight);
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const systemLogs = useTradeStore((s) => s.systemLogs);

  // ── Derive connection states ─────────────────────────────────────────
  const kafkaStatus = wsStatus === 'connected' ? 'connected' : wsStatus === 'connecting' ? 'connecting' : 'disconnected';
  const zerodhaStatus = ohlcCandles.length > 0 ? 'connected' : 'disconnected';
  const deepseekStatus = latestInsight
    ? latestInsight.headline === 'LLM API Failure' ? 'error' : 'connected'
    : 'standby';

  // ── Auto-scroll log to bottom ────────────────────────────────────────
  useEffect(() => {
    if (isExpanded && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [isExpanded, systemLogs]);

  // ── Format timestamp ─────────────────────────────────────────────────
  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const levelColor = (level: string) => {
    switch (level) {
      case 'ERROR': return 'text-red-400';
      case 'WARN': return 'text-amber-400';
      case 'INFO': return 'text-emerald-400';
      default: return 'text-slate-400';
    }
  };

  return (
    <div
      id="system-console"
      className={`flex flex-col border-t border-border-default bg-[#0B1120] transition-all duration-300 ease-in-out ${
        isExpanded ? 'h-48' : 'h-8'
      }`}
    >
      {/* ── Collapsed Status Bar ──────────────────────────────────── */}
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex h-8 shrink-0 items-center justify-between px-4 text-[11px] font-mono cursor-pointer select-none transition-colors hover:bg-slate-800/50"
      >
        {/* Left: Service indicators */}
        <div className="flex items-center gap-5">
          {/* Kafka */}
          <div className="flex items-center gap-1.5">
            <StatusDot status={kafkaStatus} />
            <Radio size={11} className="text-slate-500" />
            <span className="text-slate-400">Kafka:</span>
            <StatusLabel status={kafkaStatus} />
          </div>

          {/* Zerodha */}
          <div className="flex items-center gap-1.5">
            <StatusDot status={zerodhaStatus} />
            <Wifi size={11} className="text-slate-500" />
            <span className="text-slate-400">Zerodha:</span>
            <StatusLabel status={zerodhaStatus === 'connected' ? 'connected' : 'disconnected'} />
          </div>

          {/* DeepSeek */}
          <div className="flex items-center gap-1.5">
            <StatusDot status={deepseekStatus} />
            <Brain size={11} className="text-slate-500" />
            <span className="text-slate-400">DeepSeek:</span>
            <StatusLabel status={deepseekStatus} />
          </div>

          {/* Latency */}
          <div className="flex items-center gap-1.5">
            <Gauge size={11} className="text-slate-500" />
            <span className="text-slate-400">Latency:</span>
            <span className={`text-[10px] font-bold tabular-nums ${
              latencyMs < 50 ? 'text-emerald-400' : latencyMs < 200 ? 'text-amber-400' : 'text-red-400'
            }`}>
              {latencyMs}ms
            </span>
          </div>
        </div>

        {/* Right: Console toggle */}
        <div className="flex items-center gap-2 text-slate-500">
          <Terminal size={12} />
          <span className="text-[9px] font-semibold uppercase tracking-wider">
            System Console
          </span>
          {isExpanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
        </div>
      </button>

      {/* ── Expanded Log View ─────────────────────────────────────── */}
      {isExpanded && (
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-2 font-mono text-[11px] leading-relaxed scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-transparent">
          {systemLogs.length === 0 ? (
            <div className="flex h-full items-center justify-center text-slate-600">
              <span>No system events recorded yet. Events will appear as backend services connect.</span>
            </div>
          ) : (
            systemLogs.map((log, i) => (
              <div key={i} className="flex gap-3 py-px hover:bg-slate-800/30 transition-colors">
                <span className="text-slate-600 tabular-nums shrink-0">
                  [{formatTime(log.timestamp)}]
                </span>
                <span className={`font-bold shrink-0 w-12 ${levelColor(log.level)}`}>
                  {log.level}:
                </span>
                <span className="text-slate-300 break-all">
                  {log.message}
                </span>
              </div>
            ))
          )}
          <div ref={logEndRef} />
        </div>
      )}
    </div>
  );
}
