// @vitest-environment jsdom
//
// The custom toolbar button and its dropdown live INSIDE the TradingView iframe,
// where Tailwind's classes and the parent `<html>`'s custom properties do not
// reach — so their CSS is built as a string and injected into the iframe's head.
//
// That used to mean a second, hand-written palette: two branches of hexes copied
// out of `globals.css` (`#252525` / `#fdfcfa`, `#94a3b8` / `#5b6675`, …) selected
// by a `theme` argument. Two sources of truth for one palette, and an argument a
// caller could pass a stale value for — which is how a dark dropdown ended up
// opening over a light chart.
//
// The palette is now read from the live tokens on the parent document, so these
// tests are about one property: the injected CSS is the app's CURRENT colours, in
// either theme, with no hardcoded hex deciding it.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { injectIframeDropdownStyles, showIframeDropdown } from '../iframeDropdown';

/** The `globals.css` values for each theme, as the real stylesheet defines them. */
const DARK = {
  '--bg-surface': '#262626',
  '--bg-elevated': '#323232',
  '--border-default': '#3d3d3d',
  '--text-primary': '#f5f5f5',
  '--text-muted': '#9ca3af',
  '--color-primary': '#10b981',
};
const LIGHT = {
  '--bg-surface': '#fdfcfa',
  '--bg-elevated': '#ffffff',
  '--border-default': '#d0c8b8',
  '--text-primary': '#0f172a',
  '--text-muted': '#5b6675',
  '--color-primary': '#10b981',
};

/** Put the app into one theme: set its tokens and the `.light` marker class. */
function applyTheme(which: 'dark' | 'light') {
  const tokens = which === 'light' ? LIGHT : DARK;
  for (const [name, value] of Object.entries(tokens)) {
    document.documentElement.style.setProperty(name, value);
  }
  document.documentElement.classList.toggle('light', which === 'light');
}

function css(): string {
  return document.getElementById('tv-custom-dropdown-styles')?.textContent ?? '';
}

beforeEach(() => {
  document.head.innerHTML = '';
  document.body.innerHTML = '';
  document.documentElement.removeAttribute('style');
  document.documentElement.classList.remove('light');
});
afterEach(() => vi.useRealTimers());

describe('the injected iframe CSS follows the app’s theme', () => {
  it('uses the dark tokens in dark mode', () => {
    applyTheme('dark');
    injectIframeDropdownStyles(document);

    expect(css()).toContain('background-color: #262626'); // dropdown surface
    expect(css()).toContain('color: #f5f5f5'); // item text
    expect(css()).toContain('1px solid #3d3d3d'); // border
    // Not a single light value anywhere.
    for (const light of Object.values(LIGHT)) {
      if (Object.values(DARK).includes(light)) continue; // the shared accent
      expect(css(), `leaked the light token ${light}`).not.toContain(light);
    }
  });

  it('uses the light tokens in light mode — the reported bug', () => {
    applyTheme('light');
    injectIframeDropdownStyles(document);

    expect(css()).toContain('background-color: #fdfcfa'); // dropdown surface
    expect(css()).toContain('color: #0f172a'); // item text
    expect(css()).toContain('1px solid #d0c8b8'); // border
    // The dark surface is what showed through before; it must be gone entirely.
    expect(css()).not.toContain('#262626');
    expect(css()).not.toContain('#f5f5f5');
    expect(css()).not.toContain('#3d3d3d');
  });

  it('repaints on re-injection, so a theme switch is picked up', () => {
    applyTheme('dark');
    injectIframeDropdownStyles(document);
    expect(css()).toContain('#262626');

    applyTheme('light');
    injectIframeDropdownStyles(document);

    expect(css()).toContain('#fdfcfa');
    expect(css()).not.toContain('#262626');
    // Reuses its own <style> element rather than stacking a new one each time.
    expect(document.querySelectorAll('#tv-custom-dropdown-styles')).toHaveLength(1);
  });

  it('styles the BUTTON from the same tokens, not just the dropdown', () => {
    // The button is the other half of the report. It is in the same stylesheet, so
    // it cannot be a theme behind the panel it opens.
    applyTheme('light');
    injectIframeDropdownStyles(document);
    const rules = css();
    const btnBlock = rules.slice(
      rules.indexOf('.tv-custom-toolbar-btn {'),
      rules.indexOf('.tv-custom-dropdown {'),
    );
    expect(btnBlock).toContain(LIGHT['--text-muted']);
    expect(btnBlock).toContain(LIGHT['--bg-elevated']);
    expect(btnBlock).toContain(LIGHT['--color-primary']); // active state
    expect(btnBlock).not.toContain(DARK['--text-muted']);
  });

  it('carries no hardcoded palette to drift from globals.css', () => {
    // The specific hexes the old two-branch version hand-copied. Their absence is
    // the structural guarantee: there is one palette, and it is the app's.
    applyTheme('dark');
    injectIframeDropdownStyles(document);
    const dark = css();
    applyTheme('light');
    injectIframeDropdownStyles(document);
    const light = css();

    for (const stale of ['#252525', '#333333', '#2d2d2d', '#94a3b8', '#e8e5de', '#ddd8ce', '#f0eee9']) {
      expect(dark, `dark CSS still hardcodes ${stale}`).not.toContain(stale);
      expect(light, `light CSS still hardcodes ${stale}`).not.toContain(stale);
    }
  });

  it('falls back to the dark defaults when a token is undefined', () => {
    // No tokens set at all — must still emit valid colours rather than
    // `background-color: ;`.
    injectIframeDropdownStyles(document);
    expect(css()).not.toMatch(/:\s*;/);
    expect(css()).toContain('#262626');
  });
});

describe('the dropdown itself', () => {
  function open(active: string) {
    const btn = document.createElement('button');
    btn.className = 'tv-custom-toolbar-btn';
    document.body.appendChild(btn);
    const onSelect = vi.fn();
    showIframeDropdown(
      btn,
      [
        { value: 'linear', label: 'OLS', description: 'Linear regression baseline' },
        { value: 'curved', label: 'VWEPR', description: 'Volume-weighted polynomial' },
      ],
      active,
      onSelect,
      document,
    );
    return { btn, onSelect };
  }

  it('renders every item, and ticks only the active one', () => {
    applyTheme('light');
    open('curved');

    const items = [...document.querySelectorAll('.tv-custom-dropdown-item')];
    expect(items).toHaveLength(2);
    expect(items.map((i) => i.querySelector('span')?.textContent)).toEqual(['OLS', 'VWEPR']);
    expect(items[0].classList.contains('active')).toBe(false);
    expect(items[1].classList.contains('active')).toBe(true);

    const checks = document.querySelectorAll('.tv-custom-dropdown-item-checkmark');
    expect(checks).toHaveLength(1);
    expect(items[1].contains(checks[0])).toBe(true);
  });

  it('injects the current theme when it opens', () => {
    applyTheme('light');
    open('linear');
    expect(css()).toContain('#fdfcfa');
  });

  it('selects, then closes and releases the button', () => {
    const { btn, onSelect } = open('linear');
    expect(btn.classList.contains('active')).toBe(true);

    (document.querySelectorAll('.tv-custom-dropdown-item')[1] as HTMLElement).click();

    expect(onSelect).toHaveBeenCalledWith('curved');
    expect(document.querySelector('.tv-custom-dropdown')).toBeNull();
    expect(btn.classList.contains('active')).toBe(false);
  });

  it('never opens two panels at once', () => {
    open('linear');
    open('linear');
    expect(document.querySelectorAll('.tv-custom-dropdown')).toHaveLength(1);
  });

  it('builds labels as text, not as markup', () => {
    // The labels are static literals today, so this is a sink being closed rather
    // than a live hole — but a `<` in a label must never become an element.
    const btn = document.createElement('button');
    document.body.appendChild(btn);
    showIframeDropdown(
      btn,
      [{ value: 'x', label: '<img src=x onerror=alert(1)>', description: '<b>desc</b>' }],
      'x',
      vi.fn(),
      document,
    );

    const item = document.querySelector('.tv-custom-dropdown-item') as HTMLElement;
    expect(item.querySelector('img')).toBeNull();
    expect(item.querySelector('b')).toBeNull();
    expect(item.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});
