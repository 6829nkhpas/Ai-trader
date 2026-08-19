// @vitest-environment node

// Compliance blocker P6 — external performance surfaces removed.
//
// SEBI's advertisement code bars publishing performance representations without a
// specific set of disclosures. `docs/business/GO_TO_MARKET.md` §4 replaces them
// with process statistics: what the terminal talked the user OUT of.
//
// Two things are proved here, and the second is the one that is easy to get wrong:
//
//   1. No metric the sidebar emits is a performance figure — no return, no win
//      rate, no drawdown, no P&L, and no currency or signed percentage anywhere
//      in a label, value or tooltip. Asserted over ARBITRARY counter states, so a
//      future edit that reintroduces one fails here rather than in review.
//   2. An unmeasured metric renders "—", never "0" or "0%". A fabricated zero is
//      a claim ("you have never deviated from a plan") dressed as a measurement,
//      which is the same defect as publishing a return.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  computeDisciplineMetrics,
  type PortfolioMetric,
} from '@/hooks/useMacroIndicators';
import {
  blankDisciplineStats,
  useTradeStore,
  type DisciplineStats,
} from '@/store/useTradeStore';

const RUNS = 300;

const arbCount = fc.integer({ min: 0, max: 5_000 });

const arbStats: fc.Arbitrary<DisciplineStats> = fc.record({
  setupsAudited: arbCount,
  setupsRejected: arbCount,
  forcedHolds: arbCount,
  plansFollowed: arbCount,
  plansDeviated: arbCount,
});

/** Every string the panel puts on screen for a given stats object. */
function renderedText(metrics: PortfolioMetric[]): string {
  return metrics.map((m) => `${m.label} ${m.value} ${m.tooltip ?? ''}`).join(' | ');
}

// ── 1. No performance representation is emitted ──────────────────────────

describe('P6 — no performance figure reaches the UI', () => {
  // Terms that, in a securities context, constitute a performance
  // representation or an outcome claim.
  const BANNED = [
    'return',
    'win rate',
    'winrate',
    'drawdown',
    'p&l',
    'pnl',
    'profit',
    'loss',
    'gain',
    'expectancy',
    'accuracy',
    'roi',
    'cagr',
    'yield',
    'balance',
    'equity curve',
  ];

  it('emits no banned term for any counter state', () => {
    fc.assert(
      fc.property(arbStats, (stats) => {
        const text = renderedText(computeDisciplineMetrics(stats)).toLowerCase();
        for (const term of BANNED) {
          expect(text).not.toContain(term);
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('emits no currency amount', () => {
    fc.assert(
      fc.property(arbStats, (stats) => {
        const text = renderedText(computeDisciplineMetrics(stats));
        expect(text).not.toContain('₹');
        expect(text).not.toContain('Rs');
        expect(text).not.toMatch(/\$/);
      }),
      { numRuns: RUNS },
    );
  });

  it('emits no signed number — a leading + or - reads as profit or loss', () => {
    // The old block coloured any value starting with "+"/"-" bull/bear. The
    // colouring is gone from InvestorLayout; this keeps the DATA from
    // reintroducing the same implication.
    fc.assert(
      fc.property(arbStats, (stats) => {
        for (const metric of computeDisciplineMetrics(stats)) {
          expect(metric.value.startsWith('+')).toBe(false);
          expect(metric.value.startsWith('-')).toBe(false);
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('exposes exactly the four discipline metrics GO_TO_MARKET §4 specifies', () => {
    const labels = computeDisciplineMetrics(blankDisciplineStats()).map((m) => m.label);
    expect(labels).toEqual([
      'Setups Audited',
      'Setups Rejected',
      'Forced HOLDs',
      'Plan Adherence',
    ]);
  });

  it('emits no conviction figure — confidence must not read as expected return', () => {
    // The defect this guards is a conviction score reaching the sidebar, where a
    // "72/100" reads as an expected return (Feature Matrix #9).
    //
    // The first version of this test asserted `not.toContain('/100')` over the
    // whole rendered string, and it was WRONG — flakily, which is worse. The Plan
    // Adherence tooltip legitimately renders `${plansFollowed}/${plansResolved}`,
    // so any state where exactly 100 plans have resolved produces "…/100 plans
    // exited at their committed levels" and failed the assertion. fast-check
    // draws 100 often enough to fail roughly one run in three — a test that
    // rejects correct output on a schedule teaches people to re-run it, which is
    // how a real failure gets waved through later.
    //
    // So the score shape is asserted where a score would actually appear — the
    // metric VALUE, which is the figure rendered large — and the word is banned
    // everywhere, including tooltips.
    fc.assert(
      fc.property(arbStats, (stats) => {
        const metrics = computeDisciplineMetrics(stats);
        expect(renderedText(metrics).toLowerCase()).not.toContain('conviction');
        for (const metric of metrics) {
          expect(metric.value).not.toMatch(/\d\s*\/\s*\d/);
          expect(metric.value.toLowerCase()).not.toContain('score');
        }
      }),
      { numRuns: RUNS },
    );
  });
});

// ── 2. Honest empty, never a fabricated zero ─────────────────────────────

describe('P6 — unmeasured metrics render an em dash', () => {
  it('renders every metric as "—" on a fresh session', () => {
    const metrics = computeDisciplineMetrics(blankDisciplineStats());
    for (const metric of metrics) {
      expect(metric.value).toBe('—');
    }
  });

  it('never renders a bare zero, and only shows 0% against a real denominator', () => {
    // The invariant is not "0 never appears" — 0% adherence is a genuine
    // measurement once plans have resolved. It is "a zero is never shown for
    // something that was never measured". Counts therefore always use the em
    // dash at zero; the ratio uses it only when its denominator is empty.
    fc.assert(
      fc.property(arbStats, (stats) => {
        const plansResolved = stats.plansFollowed + stats.plansDeviated;
        for (const metric of computeDisciplineMetrics(stats)) {
          expect(metric.value).not.toBe('0');
          if (metric.label !== 'Plan Adherence' || plansResolved === 0) {
            expect(metric.value).not.toBe('0%');
          }
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('renders Plan Adherence as "—" until a deployed plan resolves', () => {
    fc.assert(
      fc.property(arbCount, arbCount, arbCount, (audited, rejected, holds) => {
        const metrics = computeDisciplineMetrics({
          setupsAudited: audited,
          setupsRejected: rejected,
          forcedHolds: holds,
          plansFollowed: 0,
          plansDeviated: 0,
        });
        const adherence = metrics.find((m) => m.label === 'Plan Adherence');
        expect(adherence?.value).toBe('—');
      }),
      { numRuns: RUNS },
    );
  });

  it('renders 0% adherence honestly once plans HAVE resolved and none was followed', () => {
    // The distinction that matters: 0% is a real measurement when the
    // denominator is real. Only an EMPTY denominator gets the em dash.
    const metrics = computeDisciplineMetrics({
      ...blankDisciplineStats(),
      plansFollowed: 0,
      plansDeviated: 3,
    });
    expect(metrics.find((m) => m.label === 'Plan Adherence')?.value).toBe('0%');
  });

  it('reports adherence as a whole percentage of resolved plans', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 500 }),
        fc.integer({ min: 0, max: 500 }),
        (followed, deviated) => {
          fc.pre(followed + deviated > 0);
          const metrics = computeDisciplineMetrics({
            ...blankDisciplineStats(),
            plansFollowed: followed,
            plansDeviated: deviated,
          });
          const value = metrics.find((m) => m.label === 'Plan Adherence')?.value ?? '';
          const expected = Math.round((followed / (followed + deviated)) * 100);
          expect(value).toBe(`${expected}%`);
          // A ratio of counts can never leave 0..100.
          expect(expected).toBeGreaterThanOrEqual(0);
          expect(expected).toBeLessThanOrEqual(100);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('is a pure function of the stats — same input, same output', () => {
    fc.assert(
      fc.property(arbStats, (stats) => {
        expect(computeDisciplineMetrics(stats)).toEqual(computeDisciplineMetrics(stats));
      }),
      { numRuns: 100 },
    );
  });
});

// ── 3. The counters themselves ───────────────────────────────────────────

describe('P6 — discipline counters record real events only', () => {
  beforeEach(() => {
    useTradeStore.setState({ disciplineStats: blankDisciplineStats() });
  });

  const stats = () => useTradeStore.getState().disciplineStats;

  it('starts at zero and reports nothing until an event is recorded', () => {
    expect(stats()).toEqual(blankDisciplineStats());
  });

  it('counts a validated FIND as audited but neither rejected nor a forced HOLD', () => {
    useTradeStore.getState().recordSetupAudit({ mode: 'FIND', actionable: true });
    expect(stats()).toMatchObject({
      setupsAudited: 1,
      setupsRejected: 0,
      forcedHolds: 0,
    });
  });

  it('counts a non-actionable FIND as a forced HOLD', () => {
    useTradeStore.getState().recordSetupAudit({ mode: 'FIND', actionable: false });
    expect(stats()).toMatchObject({
      setupsAudited: 1,
      forcedHolds: 1,
      setupsRejected: 0,
    });
  });

  it('counts a failed VERIFY as a rejected setup, not a forced HOLD', () => {
    useTradeStore.getState().recordSetupAudit({ mode: 'VERIFY', actionable: false });
    expect(stats()).toMatchObject({
      setupsAudited: 1,
      setupsRejected: 1,
      forcedHolds: 0,
    });
  });

  it('keeps rejected and forced-HOLD disjoint, so no event is double-counted', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            mode: fc.constantFrom<'FIND' | 'VERIFY'>('FIND', 'VERIFY'),
            actionable: fc.boolean(),
          }),
          { maxLength: 40 },
        ),
        (events) => {
          useTradeStore.setState({ disciplineStats: blankDisciplineStats() });
          for (const e of events) useTradeStore.getState().recordSetupAudit(e);

          const s = stats();
          expect(s.setupsAudited).toBe(events.length);
          // Every non-actionable decision lands in exactly one bucket.
          const notActionable = events.filter((e) => !e.actionable).length;
          expect(s.setupsRejected + s.forcedHolds).toBe(notActionable);
          // And neither bucket can exceed the audited total.
          expect(s.setupsRejected).toBeLessThanOrEqual(s.setupsAudited);
          expect(s.forcedHolds).toBeLessThanOrEqual(s.setupsAudited);
        },
      ),
      { numRuns: 150 },
    );
  });

  it('ignores a malformed outcome rather than inventing a count', () => {
    const store = useTradeStore.getState();
    // Cast: deliberately probing the untyped-input branch.
    store.recordSetupAudit({ mode: 'QA', actionable: true } as never);
    store.recordSetupAudit({ mode: undefined, actionable: true } as never);
    store.recordSetupAudit({} as never);
    expect(stats()).toEqual(blankDisciplineStats());
  });

  it('counts plan outcomes into the adherence ratio', () => {
    const store = useTradeStore.getState();
    store.recordPlanOutcome(true);
    store.recordPlanOutcome(true);
    store.recordPlanOutcome(false);
    expect(stats()).toMatchObject({ plansFollowed: 2, plansDeviated: 1 });
    expect(
      computeDisciplineMetrics(stats()).find((m) => m.label === 'Plan Adherence')?.value,
    ).toBe('67%');
  });

  it('clears on resetSession, so counts never survive a portfolio reset', () => {
    useTradeStore.getState().recordSetupAudit({ mode: 'FIND', actionable: false });
    useTradeStore.getState().recordPlanOutcome(false);
    expect(stats().setupsAudited).toBe(1);

    useTradeStore.getState().resetSession();
    expect(stats()).toEqual(blankDisciplineStats());
  });
});
