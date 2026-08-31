// The two rails bracket the same screen, so they have to look like one component.
//
// The right rail's collapsed state had drifted into a different visual language:
// the active destination was a filled `bg-emerald-500/10` rounded tile and hover
// painted a `bg-elevated` box, which turned a 22px glyph into a 40px app-icon
// tile sitting opposite a left rail that paints no background at all. Reported as
// "the icons look AI generated and the BG should not be there".
//
// A source-level check rather than a render, for the same reason
// `fno/__tests__/scopeBoundary.test.ts` reads its sources off disk: the invariant
// is about which classes the markup carries, and rendering `RightSidebar` would
// drag in DeepQuantPanel, OrderBook and four confluence panels to assert
// something none of them affect.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RIGHT_SIDEBAR = readFileSync(path.resolve(HERE, '../RightSidebar.tsx'), 'utf8');
const NAV_RAIL = readFileSync(path.resolve(HERE, '../../layout/NavRail.tsx'), 'utf8');

/**
 * The rail's `<nav>`, from its `aria-label` to the closing tag.
 *
 * Scoped deliberately: the open PANEL legitimately tints things (the resize
 * handle's hover state), so a whole-file scan for background classes would fail on
 * markup that is not the subject.
 */
function rail(): string {
  const start = RIGHT_SIDEBAR.indexOf('aria-label="Confluence rail"');
  const end = RIGHT_SIDEBAR.indexOf('</nav>', start);
  expect(start, 'rail block not found — did the aria-label change?').toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return RIGHT_SIDEBAR.slice(start, end);
}

/** The thin outer-edge accent bar both rails use to mark the active item. */
const ACCENT_BAR = /h-6 w-0\.5 -translate-y-1\/2 rounded-[lr]-full bg-emerald-500/;

describe('the confluence rail matches NavRail', () => {
  it('scopes to a real block, so the absence checks are not vacuous', () => {
    expect(rail().length).toBeGreaterThan(400);
    expect(rail()).toContain('destinations.map');
  });

  it('paints no background behind an icon, in any state', () => {
    // The accent bar is itself a solid `bg-emerald-500` element, so it is removed
    // before the scan — otherwise the very thing that replaced the tile would be
    // reported as one.
    const scanned = rail().replace(/absolute right-0 top-1\/2 h-6 w-0\.5[^`"']*/, '«accent-bar»');
    // `bg-surface` on the <nav> is the rail's own surface, which NavRail sets too;
    // what must not come back is a per-button fill.
    const fills = [...scanned.matchAll(/bg-[\w/.[\]-]+/g)]
      .map((m) => m[0])
      .filter((cls) => cls !== 'bg-surface');
    expect(
      fills,
      'the rail buttons must carry no background — NavRail marks its active item ' +
        'with an accent bar and colour alone',
    ).toEqual([]);
  });

  it('marks the showing destination with the same accent bar NavRail uses', () => {
    expect(rail()).toMatch(ACCENT_BAR);
    expect(NAV_RAIL, 'NavRail is the reference — if it stopped using the bar, this test is stale')
      .toMatch(ACCENT_BAR);
  });

  it('mirrors the bar to the rail’s outer edge', () => {
    // NavRail sits on the left, so its bar is on the left; this rail sits on the
    // right. A bar on the inner edge would read as belonging to the chart.
    expect(NAV_RAIL).toContain('absolute left-0 top-1/2 h-6 w-0.5');
    expect(rail()).toContain('absolute right-0 top-1/2 h-6 w-0.5');
  });

  it('uses the same glyph size and square corners as NavRail', () => {
    // No stroke-weight assertion: these two glyphs come from `react-icons`, whose
    // components take `size` and `className` but no `strokeWidth`. Size is the
    // part that has to agree for the two rails to read as one component.
    expect(rail()).toContain('size={22}');
    expect(rail()).toContain('rounded-none');
    expect(NAV_RAIL, 'the reference — a NavRail resize should surface here').toContain('size={22}');
  });
});

describe('the rail is the only switcher, and the panel gets the full height', () => {
  it('renders the rail unconditionally, not just while collapsed', () => {
    // It used to live behind `if (!sidebarOpen) return (...)`, so opening the
    // sidebar replaced the rail with an in-panel tab bar.
    expect(RIGHT_SIDEBAR).not.toMatch(/if\s*\(!sidebarOpen\)\s*\{?\s*return/);
    expect(rail()).toContain('destinations.map');
  });

  it('has no header or tab bar above the panel content', () => {
    // The two rows that used to cost the content ~62px of height.
    expect(RIGHT_SIDEBAR).not.toContain('Confluence</span>');
    expect(RIGHT_SIDEBAR).not.toContain('aria-pressed={active}');
    expect(RIGHT_SIDEBAR).not.toMatch(/border-b border-border-default/);
    expect(RIGHT_SIDEBAR).toContain('Full height — no header above it.');
  });

  it('mounts exactly one destination at a time', () => {
    // `renderSidebarContent` returns the agent OR the workspace panel, never both,
    // and it is invoked once.
    expect(RIGHT_SIDEBAR).toContain("if (sidebarTab === 'deepquant') return <DeepQuantPanel />;");
    expect([...RIGHT_SIDEBAR.matchAll(/\{renderSidebarContent\(\)\}/g)]).toHaveLength(1);
  });

  it('closes the column when the showing destination is pressed again', () => {
    // The rail is the only control, so it has to be able to give the width back.
    expect(RIGHT_SIDEBAR).toMatch(/if \(sidebarOpen && sidebarTab === tab\) \{\s*setSidebarOpen\(false\);/);
  });

  it('uses the requested glyphs', () => {
    expect(RIGHT_SIDEBAR).toContain("import { BrainAiIcon, LibraryBooksIcon } from './sidebarIcons';");
    expect(RIGHT_SIDEBAR).toContain('Icon: LibraryBooksIcon');
    expect(RIGHT_SIDEBAR).toContain('Icon: BrainAiIcon');
  });

  it('imports no `react-icons` barrel, which breaks the turbopack build', () => {
    // `react-icons` v5 has no per-icon entry points, so any set import drags the
    // whole set (~4000 modules for `md`) into the client graph and overflows
    // turbopack's 10-bit module header:
    //   "The high bits of the position 2248738 are not all 0s or 1s"
    // `next build` tolerates it; `next build --turbopack` — which `build:web` and
    // the Docker image use — does not, so it only fails on the server.
    expect(RIGHT_SIDEBAR).not.toMatch(/from 'react-icons/);
    const icons = readFileSync(path.resolve(HERE, '../sidebarIcons.tsx'), 'utf8');
    expect(icons).not.toMatch(/from 'react-icons/);
    // The art is still the requested pair, so the paths must actually be there.
    expect(icons).toContain('export function BrainAiIcon');
    expect(icons).toContain('export function LibraryBooksIcon');
    expect(icons.match(/<path/g)?.length).toBeGreaterThanOrEqual(3);
  });
});
