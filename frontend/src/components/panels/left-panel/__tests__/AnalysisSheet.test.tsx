// @vitest-environment jsdom

/**
 * `AnalysisSheet` — the Market Watch detail sheet.
 *
 * The sheet is the only place the full analyses now live, so the tests cover the
 * two things that would make it unusable rather than merely ugly:
 *
 * - It must render OUTSIDE the panel's DOM subtree. The Market Watch column in
 *   `TerminalLayout` carries an inline `transform: translateX(...)`, and a
 *   transformed ancestor becomes the containing block for `position: fixed`
 *   descendants — an in-place sheet would be clipped into a 224px column. The
 *   portal assertion here is what stops that regression from silently returning.
 * - It must be dismissable by Escape and by the close button, and hand focus back
 *   to the strip that opened it, because the strips are the only way back in.
 */

import React, { useRef, useState } from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import AnalysisSheet, { type AnalysisTab } from '../AnalysisSheet';
import {
  useQuantStore,
  type ChartPattern,
  type ConsensusReport,
  type MultiTfChartPatterns,
  type SentimentPayload,
} from '../../../../store/useQuantStore';
import { useRadarStore } from '../../../../store/useRadarStore';
import { useTradeStore } from '../../../../store/useTradeStore';

afterEach(cleanup);

const SENTIMENT: SentimentPayload = {
  symbol: 'RELIANCE',
  score: 42,
  label: 'Bullish',
  top_headline: 'Order book at record high',
  impact: 'positive',
  headlines: ['Order book at record high', 'Refinery margins expand', 'Retail arm adds stores'],
};

const CONSENSUS: ConsensusReport = {
  symbol: 'RELIANCE',
  trend_score: 61,
  momentum_state: 'OVERBOUGHT',
  volatility_state: 'EXPANDING',
  volume_flow_state: 'ACCUMULATION',
  active_patterns: ['Bullish Engulfing'],
  active_strategies: ['Golden Cross'],
};

/**
 * A fixed computation time for the consensus fixture.
 *
 * A module constant rather than `Date.now()` in the harness body: calling it
 * during render makes the rendered output depend on the clock, so a re-render
 * would quietly change the reading's age.
 */
const COMPUTED_AT = 1_700_000_000_000;

/**
 * A stand-in for the real panel: a transformed, overflow-hidden container with the
 * trigger inside it — the exact arrangement that clips a non-portaled sheet.
 */
function Harness({
  initialTab = null,
  symbol = 'RELIANCE',
  consensus = CONSENSUS,
  multiTfPatterns = null,
  patternsError = null,
}: {
  initialTab?: AnalysisTab | null;
  symbol?: string;
  consensus?: ConsensusReport | null;
  multiTfPatterns?: MultiTfChartPatterns[] | null;
  patternsError?: string | null;
}) {
  const [tab, setTab] = useState<AnalysisTab | null>(initialTab);
  const panelRef = useRef<HTMLDivElement>(null);

  return (
    <div
      ref={panelRef}
      data-testid="panel-subtree"
      style={{ width: 224, overflow: 'hidden', transform: 'translateX(0)' }}
    >
      <button type="button" onClick={() => setTab('sentiment')}>
        open sentiment
      </button>
      <button type="button" onClick={() => setTab('technical')}>
        open technical
      </button>
      <button type="button" onClick={() => setTab('patterns')}>
        open patterns
      </button>

      <AnalysisSheet
        tab={tab}
        onTabChange={setTab}
        onClose={() => setTab(null)}
        symbol={symbol}
        sentiment={SENTIMENT}
        isSentimentLoading={false}
        sentimentError={null}
        consensus={consensus}
        consensusComputedAt={COMPUTED_AT}
        multiTfPatterns={multiTfPatterns}
        isPatternsLoading={false}
        patternsError={patternsError}
      />
    </div>
  );
}

describe('AnalysisSheet', () => {
  it('stays closed while tab is null', () => {
    render(<Harness />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens as a modal dialog when a strip sets a tab', async () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole('button', { name: 'open sentiment' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'AI News Sentiment' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('renders outside the panel subtree so a transformed ancestor cannot clip it', async () => {
    render(<Harness initialTab="sentiment" />);

    const dialog = await screen.findByRole('dialog');
    const panelSubtree = screen.getByTestId('panel-subtree');

    expect(panelSubtree.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
  });

  it('shows the full headline list without requiring a second click', async () => {
    render(<Harness initialTab="sentiment" />);

    await screen.findByRole('dialog');
    for (const headline of SENTIMENT.headlines) {
      // `getAllByText`, not `getByText`: the leading headline is also the block's
      // summary line, so it legitimately appears twice.
      expect(screen.getAllByText(headline).length).toBeGreaterThan(0);
    }
  });

  it('names the symbol the analysis belongs to', async () => {
    render(<Harness initialTab="sentiment" />);

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('RELIANCE');
  });

  it('closes on Escape', async () => {
    render(<Harness initialTab="sentiment" />);

    const dialog = await screen.findByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('closes on the close button', async () => {
    render(<Harness initialTab="sentiment" />);

    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: 'Close details' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('renders the full technical HUD on the technical tab', async () => {
    render(<Harness initialTab="technical" />);

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('+61');
    expect(dialog).toHaveTextContent('STRONG BULL');
    // Strategies and patterns fold into this tab rather than getting strips of
    // their own, so they have to actually be here.
    expect(screen.getByText('Bullish Engulfing')).toBeInTheDocument();
    expect(screen.getByText('Golden Cross')).toBeInTheDocument();
  });

  it('explains the absence instead of rendering another symbol\u2019s consensus', async () => {
    render(<Harness initialTab="technical" symbol="TCS" consensus={CONSENSUS} />);

    const dialog = await screen.findByRole('dialog');
    expect(dialog).not.toHaveTextContent('+61');
    expect(dialog).toHaveTextContent(/No technical reading for/);
    expect(dialog).toHaveTextContent(/Run Deep Quant Analysis/);
  });

  it.each([
    ['open sentiment', 'AI News Sentiment'],
    ['open technical', 'Technical Consensus'],
    ['open patterns', 'Pattern Scanner'],
  ])('opens on the tab the %s strip asked for', async (triggerName, tabName) => {
    render(<Harness />);

    fireEvent.click(screen.getByRole('button', { name: triggerName }));

    await screen.findByRole('dialog');
    expect(screen.getByRole('tab', { name: tabName })).toHaveAttribute('aria-selected', 'true');
  });

  it('switches sections without closing the sheet', async () => {
    render(<Harness initialTab="sentiment" />);

    const dialog = await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('tab', { name: 'Technical Consensus' }));

    expect(screen.getByRole('tab', { name: 'Technical Consensus' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(dialog).toHaveTextContent('+61');
    // Still the same dialog instance — switching tabs must not tear the sheet down
    // and rebuild it, which would drop scroll position and focus.
    expect(screen.getByRole('dialog')).toBe(dialog);
  });

  it('keeps exactly one tab in the tab order, so the arrow keys own the rest', async () => {
    render(<Harness initialTab="technical" />);

    await screen.findByRole('dialog');
    const tabs = screen.getAllByRole('tab');

    // Roving tabindex: Tab reaches the tablist once, then Left/Right move within
    // it. Asserting the tabindex pattern rather than simulating arrow presses —
    // the traversal itself is Base UI's composite behaviour, and jsdom's
    // synthetic keydown does not drive its internal highlight state, so a
    // keyboard simulation here would be testing the harness, not the wiring.
    const inTabOrder = tabs.filter((t) => t.getAttribute('tabindex') === '0');
    expect(inTabOrder).toHaveLength(1);
    expect(inTabOrder[0]).toHaveAttribute('aria-selected', 'true');
    expect(inTabOrder[0]).toHaveAccessibleName('Technical Consensus');
    for (const other of tabs.filter((t) => t !== inTabOrder[0])) {
      expect(other).toHaveAttribute('tabindex', '-1');
    }
  });

  it('pairs every tab with the panel it controls', async () => {
    render(<Harness initialTab="technical" />);

    await screen.findByRole('dialog');
    const tab = screen.getByRole('tab', { name: 'Technical Consensus' });
    const panel = screen.getByRole('tabpanel');

    // The wiring that lets a screen reader move from a tab to its content.
    expect(tab).toHaveAttribute('aria-controls', panel.id);
    expect(panel).toHaveAttribute('aria-labelledby', tab.id);
  });

  it('flags a section that could not be read, rather than badging it as empty', async () => {
    render(<Harness initialTab="sentiment" patternsError="Tool server unreachable" />);

    await screen.findByRole('dialog');
    const patternsTab = screen.getByRole('tab', { name: 'Pattern Scanner' });
    // No count badge: a scan that failed has no count to report.
    expect(patternsTab.textContent).not.toMatch(/\d/);
  });

  it('badges the tabs with what each section is reporting', async () => {
    render(
      <Harness
        initialTab="sentiment"
        multiTfPatterns={[{ timeframe: '10m', patterns: [] }, { timeframe: '1h', patterns: [] }]}
      />,
    );

    await screen.findByRole('dialog');
    // 3 headlines in the fixture, trend score +61.
    expect(screen.getByRole('tab', { name: 'AI News Sentiment' })).toHaveTextContent('3');
    expect(screen.getByRole('tab', { name: 'Technical Consensus' })).toHaveTextContent('+61');
  });

  it('keeps the pattern scanner interactive inside the sheet', async () => {
    // The scanner reads the quant store directly and pushes a chart overlay on
    // click. Moving it behind a portal could easily have left the cards rendering
    // but inert, so the click path is exercised from where it now lives.
    const scannedPattern: ChartPattern = {
      pattern_type: 'Falling Wedge',
      sentiment: 'bullish',
      confidence: 0.82,
      start_idx: 40,
      end_idx: 55,
      description: 'Converging downward channel',
      structural_bias: 'DOWNTREND',
      geometric_strictness: 0.7,
      volume_validation: 'CONFIRMED',
      breakout_status: 'PENDING',
      time: 1_700_000_000,
      start_time: 1_699_000_000,
      high: 2500,
      low: 2400,
    };
    const scan: MultiTfChartPatterns[] = [{ timeframe: '10m', patterns: [scannedPattern] }];

    useTradeStore.setState({ selectedSymbol: 'RELIANCE' });
    useRadarStore.setState({ vizTarget: null });
    useQuantStore.setState({
      multiTfPatterns: scan,
      isFetchingPatterns: false,
      patternsError: null,
    });

    render(<Harness initialTab="patterns" />);

    await screen.findByRole('dialog');
    const card = screen.getByRole('button', { name: /Falling Wedge/ });
    fireEvent.click(card);

    const target = useRadarStore.getState().vizTarget;
    expect(target).not.toBeNull();
    expect(target?.symbol).toBe('RELIANCE');
    expect(target?.kind).toBe('pattern');
    expect(target?.pattern?.name).toBe('Falling Wedge');
  });

  it('reports a failed scan inside the sheet instead of an empty market', async () => {
    useQuantStore.setState({
      multiTfPatterns: null,
      isFetchingPatterns: false,
      patternsError: 'Tool server unreachable',
    });

    render(<Harness initialTab="patterns" />);

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Scan unavailable');
    expect(dialog).toHaveTextContent('Tool server unreachable');
    expect(dialog).not.toHaveTextContent('No patterns forming');
  });

  it('moves focus into the sheet on open and back to the trigger on close', async () => {
    render(<Harness />);

    const trigger = screen.getByRole('button', { name: 'open sentiment' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    fireEvent.keyDown(dialog, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    // The strips are the only way back into the sheet, so focus has to land on one.
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
