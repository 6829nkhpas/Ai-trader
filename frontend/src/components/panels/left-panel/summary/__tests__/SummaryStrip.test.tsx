// @vitest-environment jsdom

/**
 * `SummaryStrip` — the row primitive behind the Market Watch summary rail.
 *
 * The rail replaced three tall inline blocks, so each row is now the ONLY thing
 * standing between the user and a wrong read of the market. These tests pin the
 * parts that carry that responsibility:
 *
 * - a `ready` row shows its caller's value, and announces it as text (the
 *   visible value is colour-coded JSX and would otherwise be unspeakable);
 * - an `error` row says the reading is unavailable AND carries the failure text,
 *   rather than rendering a plausible-looking value;
 * - a `loading` row is exposed as a live status, not as a value;
 * - the row is a real `<button type="button">`, which is what makes it
 *   keyboard-operable and announced as a dialog trigger.
 */

import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { Newspaper } from 'lucide-react';

import SummaryStrip from '../SummaryStrip';

afterEach(cleanup);

describe('SummaryStrip', () => {
  it('renders the value slot and the second-line detail in the ready state', () => {
    render(
      <SummaryStrip
        icon={<Newspaper size={10} />}
        label="AI News Sentiment"
        valueText="Bullish, score +42"
        value={<span>+42</span>}
        detail={<span>Order book at record high</span>}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('+42')).toBeInTheDocument();
    expect(screen.getByText('Order book at record high')).toBeInTheDocument();
    // The reading reaches assistive tech as words, not just as a coloured pill.
    expect(
      screen.getByRole('button', { name: /AI News Sentiment, Bullish, score \+42\. Open details\./ }),
    ).toBeInTheDocument();
  });

  it('is a native button that advertises the dialog it opens', () => {
    render(
      <SummaryStrip
        icon={<Newspaper size={10} />}
        label="Patterns"
        valueText="4 patterns"
        value={<span>4</span>}
        onClick={() => {}}
      />,
    );

    const strip = screen.getByRole('button');
    // Native <button> semantics are what give Enter/Space activation and focus
    // order for free. A div with onClick would look identical and be unreachable
    // by keyboard, so the element type itself is the assertion.
    expect(strip.tagName).toBe('BUTTON');
    expect(strip).toHaveAttribute('type', 'button');
    expect(strip).toHaveAttribute('aria-haspopup', 'dialog');
  });

  it('invokes onClick when activated', () => {
    const onClick = vi.fn();
    render(
      <SummaryStrip
        icon={<Newspaper size={10} />}
        label="Technical Consensus"
        valueText="Bullish"
        value={<span>+61</span>}
        onClick={onClick}
      />,
    );

    fireEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('surfaces the failure text in the error state and renders no value', () => {
    render(
      <SummaryStrip
        icon={<Newspaper size={10} />}
        label="AI News Sentiment"
        state="error"
        errorMessage="Sentiment service returned 503"
        // A caller may still be holding a previous reading; the error state must
        // win, because showing it would present a stale number as current.
        value={<span>+42</span>}
        valueText="Bullish, score +42"
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('Sentiment service returned 503')).toBeInTheDocument();
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText('+42')).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Sentiment service returned 503/ }),
    ).toBeInTheDocument();
  });

  it('exposes the loading state as a status and renders no value', () => {
    render(
      <SummaryStrip
        icon={<Newspaper size={10} />}
        label="Patterns"
        state="loading"
        loadingMessage="Scanning"
        value={<span>4</span>}
        onClick={() => {}}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Scanning');
    expect(screen.queryByText('4')).not.toBeInTheDocument();
  });

  it('states the empty case in its own words rather than borrowing a neutral reading', () => {
    render(
      <SummaryStrip
        icon={<Newspaper size={10} />}
        label="Technical Consensus"
        state="empty"
        emptyMessage="Run Deep Quant"
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('Run Deep Quant')).toBeInTheDocument();
    expect(screen.queryByText(/neutral/i)).not.toBeInTheDocument();
    // Still clickable: the sheet explains what to run and why.
    expect(screen.getByRole('button')).toBeEnabled();
  });
});
