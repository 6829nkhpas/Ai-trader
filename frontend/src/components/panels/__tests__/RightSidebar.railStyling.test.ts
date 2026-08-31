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
 * The collapsed-rail branch of `RightSidebar`, from its `aria-label` to the
 * closing `</nav>`.
 *
 * Scoped deliberately: the EXPANDED sidebar legitimately tints things (the resize
 * handle's hover, the header's `bg-elevated/10`), so a whole-file scan for
 * background classes would fail on markup that is not the subject.
 */
function collapsedRail(): string {
  const start = RIGHT_SIDEBAR.indexOf('Confluence rail (collapsed)');
  const end = RIGHT_SIDEBAR.indexOf('</nav>', start);
  expect(start, 'collapsed rail block not found — did the aria-label change?').toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return RIGHT_SIDEBAR.slice(start, end);
}

/** The thin outer-edge accent bar both rails use to mark the active item. */
const ACCENT_BAR = /h-6 w-0\.5 -translate-y-1\/2 rounded-[lr]-full bg-emerald-500/;

describe('the collapsed confluence rail matches NavRail', () => {
  it('scopes to a real block, so the absence checks are not vacuous', () => {
    const rail = collapsedRail();
    expect(rail.length).toBeGreaterThan(400);
    expect(rail).toContain('destinations.map');
  });

  it('paints no background behind an icon, in any state', () => {
    // The accent bar is itself a solid `bg-emerald-500` element, so it is removed
    // before the scan — otherwise the very thing that replaced the tile would be
    // reported as one.
    const rail = collapsedRail().replace(
      /absolute right-0 top-1\/2 h-6 w-0\.5[^`"']*/,
      '«accent-bar»',
    );
    // `bg-surface` on the <nav> is the rail's own surface, which NavRail sets too;
    // what must not come back is a per-button fill.
    const fills = [...rail.matchAll(/bg-[\w/.[\]-]+/g)]
      .map((m) => m[0])
      .filter((cls) => cls !== 'bg-surface');
    expect(
      fills,
      'the rail buttons must carry no background — NavRail marks its active item ' +
        'with an accent bar and colour alone',
    ).toEqual([]);
  });

  it('marks the active destination with the same accent bar NavRail uses', () => {
    expect(collapsedRail()).toMatch(ACCENT_BAR);
    expect(NAV_RAIL, 'NavRail is the reference — if it stopped using the bar, this test is stale')
      .toMatch(ACCENT_BAR);
  });

  it('mirrors the bar to the rail’s outer edge', () => {
    // NavRail sits on the left, so its bar is on the left; this rail sits on the
    // right. A bar on the inner edge would read as belonging to the chart.
    expect(NAV_RAIL).toContain('absolute left-0 top-1/2 h-6 w-0.5');
    expect(collapsedRail()).toContain('absolute right-0 top-1/2 h-6 w-0.5');
  });

  it('uses the same glyph size, stroke weight and square corners as NavRail', () => {
    const rail = collapsedRail();
    expect(rail).toContain('size={22}');
    expect(rail).toContain('strokeWidth={isActive ? 2.4 : 2}');
    expect(rail).toContain('rounded-none');
    // The reference, so a NavRail change shows up here rather than silently
    // letting the two drift apart again.
    expect(NAV_RAIL).toContain('size={22}');
    expect(NAV_RAIL).toContain('strokeWidth={isActive ? 2.4 : 2}');
  });

  it('keeps no tinted tile behind the expanded header’s profile glyph', () => {
    // Same complaint, same panel: the header icon sat in a filled emerald square.
    expect(RIGHT_SIDEBAR).not.toMatch(/bg-emerald-500\/1\d?\s[^"'`]*>\s*<ProfileIcon/);
    expect(RIGHT_SIDEBAR).toContain('className="shrink-0 text-emerald-500 dark:text-emerald-400"');
  });
});
