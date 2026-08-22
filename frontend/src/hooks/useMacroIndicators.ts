// hooks/useMacroIndicators.ts — Live Indian market macro indicators via Kite Quote API
//
// Follows the exact same architecture as WatchlistPanel.tsx:
//   1. Define index symbols to track
//   2. Poll /kite/quote every 30s
//   3. Return structured data for the MacroSentimentPanel
//
// No new backend endpoints needed — reuses the existing Kite REST proxy on :8084.

import { useState, useEffect, useCallback, useRef } from 'react';
import { useTradeStore, type DisciplineStats } from '../store/useTradeStore';
import { kiteFetch } from '../lib/kiteFetch';

// ── Index Definitions ────────────────────────────────────────────────────────
// These are NSE indices available via Kite Connect's quote API.

export interface MacroIndex {
  /** Kite-format instrument key, e.g. "NSE:NIFTY 50" */
  kiteKey: string;
  /** Display label in the panel */
  label: string;
  /** Short category tag */
  category: 'Benchmark' | 'Sectoral' | 'Volatility';
}

export const MACRO_INDICES: MacroIndex[] = [
  { kiteKey: 'NSE:NIFTY 50',           label: 'NIFTY 50',       category: 'Benchmark' },
  { kiteKey: 'NSE:NIFTY BANK',         label: 'BANK NIFTY',     category: 'Benchmark' },
  { kiteKey: 'NSE:INDIA VIX',          label: 'INDIA VIX',      category: 'Volatility' },
  { kiteKey: 'NSE:NIFTY IT',           label: 'NIFTY IT',       category: 'Sectoral' },
  { kiteKey: 'NSE:NIFTY FIN SERVICE',  label: 'NIFTY FIN SVC',  category: 'Sectoral' },
];



// ── Quote Data (mirrors WatchlistPanel's QuoteData) ──────────────────────────

export interface MacroQuote {
  symbol: string;
  last_price: number;
  open: number;
  high: number;
  low: number;
  close: number; // previous close
  volume: number;
  change: number; // % change
  net_change: number;
}

// ── Enriched indicator for rendering ─────────────────────────────────────────

export interface MacroIndicator {
  label: string;
  category: MacroIndex['category'];
  value: string;
  change: string;
  direction: 'up' | 'down' | 'flat';
  raw: MacroQuote | null;
}

// ── Portfolio Risk Metrics ───────────────────────────────────────────────────

export interface PortfolioMetric {
  label: string;
  value: string;
  tooltip?: string;
}

// ── Hook Return Type ─────────────────────────────────────────────────────────

interface UseMacroIndicatorsReturn {
  indicators: MacroIndicator[];
  portfolioMetrics: PortfolioMetric[];
  loading: boolean;
  error: string | null;
  lastUpdated: number | null;
}

// ── Helper: format index price ───────────────────────────────────────────────

function formatIndexPrice(price: number, label: string): string {
  // VIX is a small number, show 2 decimals
  if (label.includes('VIX')) {
    return price.toFixed(2);
  }
  // Indices: show with comma separator, no decimals for large values
  if (price >= 1000) {
    return price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return price.toFixed(2);
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useMacroIndicators(): UseMacroIndicatorsReturn {
  const [quotes, setQuotes] = useState<Record<string, MacroQuote>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  // Discipline statistics for the sidebar summary (compliance blocker P6).
  // `executedTrades` / `portfolioBalance` are deliberately NOT read here any
  // more: they fed the removed performance metrics.
  const disciplineStats = useTradeStore((s) => s.disciplineStats);

  // ── Fetch macro quotes ─────────────────────────────────────────────────
  const fetchMacroQuotes = useCallback(async () => {
    try {
      // Build query params identical to WatchlistPanel pattern
      const params = MACRO_INDICES.map((idx) => `i=${encodeURIComponent(idx.kiteKey)}`).join('&');
      const res = await kiteFetch(`/quote?${params}`);

      if (!res.ok) {
        throw new Error(`Kite quote API returned ${res.status}`);
      }

      const data = await res.json();

      if (data.quotes && Array.isArray(data.quotes)) {
        const map: Record<string, MacroQuote> = {};
        for (const q of data.quotes) {
          map[q.symbol] = q;
        }
        setQuotes(map);
        setLastUpdated(Date.now());
        setError(null);
      }
    } catch (err: any) {
      console.error('[MacroIndicators] Quote fetch failed:', err);
      // Fail silently and let the simulation run
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll on mount + every 30s (same cadence as WatchlistPanel)
  useEffect(() => {
    fetchMacroQuotes();
    intervalRef.current = setInterval(fetchMacroQuotes, 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchMacroQuotes]);



  // ── Build enriched indicators ──────────────────────────────────────────
  const indicators: MacroIndicator[] = MACRO_INDICES.map((idx) => {
    // Extract the symbol portion from kiteKey (e.g. "NSE:NIFTY 50" → "NIFTY 50")
    const symbol = idx.kiteKey.split(':')[1] || idx.kiteKey;
    const quote = quotes[symbol] ?? null;

    if (!quote) {
      return {
        label: idx.label,
        category: idx.category,
        value: '—',
        change: '',
        direction: 'flat' as const,
        raw: null,
      };
    }

    const direction: 'up' | 'down' | 'flat' =
      quote.change > 0.01 ? 'up' : quote.change < -0.01 ? 'down' : 'flat';

    return {
      label: idx.label,
      category: idx.category,
      value: formatIndexPrice(quote.last_price, idx.label),
      change: `${quote.change >= 0 ? '+' : ''}${quote.change.toFixed(2)}%`,
      direction,
      raw: quote,
    };
  });

  // ── Compute discipline metrics from live store data ────────────────────
  const portfolioMetrics: PortfolioMetric[] = computeDisciplineMetrics(disciplineStats);

  return { indicators, portfolioMetrics, loading, error, lastUpdated };
}

// ── Discipline metrics computation ───────────────────────────────────────────
//
// Compliance blocker P6 — external performance surfaces removed.
//
// This function previously reported Total Return, Win Rate, Max Drawdown and
// Avg Conviction. All four are gone:
//
//   - Total Return / Win Rate / Max Drawdown are performance representations.
//     SEBI's advertisement code bars publishing them without a specific set of
//     disclosures, and the versions computed here could not have carried them:
//     they were derived from PAPER trades against a hardcoded ₹1,00,000 opening
//     balance, so they described a simulation, not any user's actual result.
//     (The old win-rate branch also counted any trade with a positive price as a
//     win, which was not a win rate at all.)
//   - Avg Conviction invited reading the model's confidence as an expected
//     return, which is the specific misreading the AI-disclosure requirement
//     exists to prevent.
//
// They are replaced by the process statistics `docs/business/GO_TO_MARKET.md` §4
// specifies: what the terminal talked the user OUT of. Every value renders "—"
// until a real event has been counted; nothing is inferred and no zero is
// presented as a measurement.
//
// Win rate and expectancy are NOT removed from the product — they remain in
// `agents/deep-quant-loop/journal.py` as internal per-setup calibration, which
// is model monitoring the AI framework actively wants.
//
// [COUNSEL] GO_TO_MARKET §4 and PLAN_OF_ACTION §11 both require sign-off on the
// exact wording of any user-facing discipline summary before it ships. The labels
// below are deliberately factual counts with no comparative or outcome claim.

// Exported for the compliance test suite: P6 requires proof that no performance
// figure is emitted and that an unmeasured metric renders "—" rather than 0.
export function computeDisciplineMetrics(stats: DisciplineStats): PortfolioMetric[] {
  const { setupsAudited, setupsRejected, forcedHolds, plansFollowed, plansDeviated } =
    stats;

  const plansResolved = plansFollowed + plansDeviated;
  const adherence =
    plansResolved > 0 ? Math.round((plansFollowed / plansResolved) * 100) : null;

  return [
    {
      label: 'Setups Audited',
      value: setupsAudited > 0 ? `${setupsAudited}` : '—',
      tooltip: 'Trade setups this terminal has analysed in this session',
    },
    {
      label: 'Setups Rejected',
      value: setupsRejected > 0 ? `${setupsRejected}` : '—',
      tooltip: 'Your proposed trades that failed the risk validator',
    },
    {
      label: 'Forced HOLDs',
      value: forcedHolds > 0 ? `${forcedHolds}` : '—',
      tooltip: 'Analyses that concluded no trade was worth taking',
    },
    {
      label: 'Plan Adherence',
      // Null until a deployed plan has actually resolved at its committed stop
      // or target. An unmeasured metric shows "—", never 0%.
      value: adherence !== null ? `${adherence}%` : '—',
      tooltip:
        plansResolved > 0
          ? `${plansFollowed}/${plansResolved} plans exited at their committed levels`
          : 'No deployed plan has resolved yet',
    },
  ];
}
