// @vitest-environment jsdom

/**
 * F&O Frontend Section (F4) — component/snapshot test for `OptionsHud` (task 7.5).
 *
 * Validates (Requirements 5.3, 8.2):
 * - Every `null` HUD field renders as an explicit N/A badge, never a fabricated
 *   value (R5.3, R8.2).
 * - The agent bias state renders the correct human string for each of
 *   bullish / bearish / neutral / N/A (R5.2).
 * - The chain context renders the correct string for own-chain vs broad-market
 *   vs N/A (R5.4).
 *
 * `OptionsHud` is pure presentation over a `HudModel`, so no chart/transport
 * mocking is required — only jsdom rendering.
 */

import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, within, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import { OptionsHud } from '../OptionsHud';
import type { HudModel, OptionsBiasState, ChainContext } from '../viewModel';

/** A HUD model whose every analytic leaf is null (fully unavailable). */
function allNullHud(
  overrides: Partial<HudModel> = {},
): HudModel {
  return {
    pcrOi: null,
    pcrVolume: null,
    maxPain: null,
    aggregateOiBias: { call: null, put: null },
    walls: { support: null, resistance: null },
    ivSkew: null,
    futuresBasis: null,
    biasState: null,
    biasSignals: null,
    context: { underlying: 'NIFTY 50', expiry: '2024-12-26', chainContext: null },
    ...overrides,
  };
}

/** A fully-populated HUD model with finite values everywhere. */
function fullHud(overrides: Partial<HudModel> = {}): HudModel {
  return {
    pcrOi: 1.18,
    pcrVolume: 0.94,
    maxPain: 24000,
    aggregateOiBias: { call: 'short_buildup', put: 'long_unwinding' },
    walls: { support: 23800, resistance: 24200 },
    ivSkew: { putMinusCall: 0.021, slope: -0.0003, atmIv: 0.132 },
    futuresBasis: 12.5,
    biasState: 'bullish',
    biasSignals: { max_pain_vs_spot: 'below', futures_basis_sign: 'positive' },
    context: { underlying: 'NIFTY 50', expiry: '2024-12-26', chainContext: 'own-chain' },
    ...overrides,
  };
}

describe('OptionsHud (component)', () => {
  afterEach(() => cleanup());

  it('renders N/A badges for null fields and never fabricates a value (R5.3, R8.2)', () => {
    render(<OptionsHud hud={allNullHud()} />);

    // Many fields are N/A: PCR (OI/volume), max pain, futures basis, call/put
    // buildup, walls (support/resistance), IV-skew, bias state, signals, chain
    // context — assert a generous lower bound of explicit N/A badges.
    const naBadges = screen.getAllByText('N/A');
    expect(naBadges.length).toBeGreaterThanOrEqual(10);

    // A fabricated zero must never appear in place of a missing value.
    expect(screen.queryByText('0')).toBeNull();
  });

  it('renders finite analytic values rather than N/A when present (R5.1)', () => {
    render(<OptionsHud hud={fullHud()} />);

    expect(screen.getByText('1.18')).toBeInTheDocument(); // PCR OI
    expect(screen.getByText('0.94')).toBeInTheDocument(); // PCR volume
    expect(screen.getByText('short_buildup')).toBeInTheDocument();
    expect(screen.getByText('long_unwinding')).toBeInTheDocument();
  });

  it.each<[OptionsBiasState | null, string]>([
    ['bullish', 'Bullish'],
    ['bearish', 'Bearish'],
    ['neutral', 'Neutral'],
    [null, 'N/A'],
  ])('renders the correct bias string for state=%s (R5.2)', (state, label) => {
    render(<OptionsHud hud={fullHud({ biasState: state })} />);

    // The bias badge lives in the "Agent Options Bias" section header row.
    const heading = screen.getByText('Agent Options Bias');
    const section = heading.closest('div')?.parentElement as HTMLElement;
    expect(within(section).getByText(label)).toBeInTheDocument();
  });

  it.each<[ChainContext | null, string]>([
    ['own-chain', 'Own chain'],
    ['broad-market', 'Broad-market benchmark'],
  ])('renders the correct chain-context string for %s (R5.4)', (chainContext, label) => {
    render(
      <OptionsHud
        hud={fullHud({ context: { underlying: 'NIFTY 50', expiry: '2024-12-26', chainContext } })}
      />,
    );
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('renders the underlying and expiry chain context header (R5.4)', () => {
    render(<OptionsHud hud={fullHud()} />);
    expect(screen.getByText('NIFTY 50')).toBeInTheDocument();
    expect(screen.getByText('2024-12-26')).toBeInTheDocument();
  });

  it('matches the snapshot for a fully-populated HUD', () => {
    const { asFragment } = render(<OptionsHud hud={fullHud()} />);
    expect(asFragment()).toMatchSnapshot();
  });

  it('matches the snapshot for an all-null (unavailable) HUD', () => {
    const { asFragment } = render(<OptionsHud hud={allNullHud()} />);
    expect(asFragment()).toMatchSnapshot();
  });
});
