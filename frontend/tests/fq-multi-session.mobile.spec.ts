// tests/fq-multi-session.mobile.spec.ts
//
// The 360 px assertions, which can only be made here.
//
// The migration plan asks that at 360 px the composer and the plan stay readable and the tabs scroll
// horizontally. jsdom has no layout engine, so a unit test claiming an element is "visible at 360 px"
// would be fiction — `toBeVisible` there checks the DOM, not the geometry. A real browser at a real
// viewport is the only place this means anything, so it is asserted here and deliberately absent from
// the vitest suite.

import { test, expect, type Page } from '@playwright/test';

async function signIn(page: Page, token: string) {
  await page.context().addCookies([
    {
      name: 'access_token',
      value: token,
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    },
  ]);
}

/** Whether an element is inside the viewport, not merely present in the DOM. */
async function isWithinViewport(page: Page, selector: ReturnType<Page['getByRole']>) {
  const box = await selector.boundingBox();
  const size = page.viewportSize();
  if (!box || !size) return false;
  return box.x >= 0 && box.x + box.width <= size.width + 1;
}

test.describe('the workspace at 360 px', () => {
  test.beforeEach(async ({ page }) => {
    // Own user per test, for the same reason as the desktop spec: sessions are per-user and the agent's
    // database outlives a single test, so a shared identity makes absolute tab counts depend on test order.
    await signIn(
      page,
      `e2e-${test.info().title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40)}`,
    );
    page.on('pageerror', (err) => {
      throw new Error(`uncaught page error: ${err.message}`);
    });
  });

  test('the composer stays reachable and nothing overflows the width', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Show AI Agent panel' }).click();
    await page.getByRole('button', { name: 'New analysis session' }).click();
    await page.locator('#btn-run-deep-quant').click();
    await expect(page.getByText(/Momentum is intact/)).toBeVisible({ timeout: 30_000 });

    // The composer is the control the whole surface exists to reach. Pinned above the keyboard
    // inset, it must be on screen rather than pushed below the fold by the transcript.
    const composer = page.getByRole('textbox').first();
    await expect(composer).toBeVisible();
    expect(await isWithinViewport(page, composer)).toBe(true);

    // Horizontal overflow at 360 px is the classic symptom of a fixed-width child, and it makes the
    // whole page pan sideways.
    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflows).toBe(false);
  });

  test('the tab strip scrolls instead of squashing the tabs', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Show AI Agent panel' }).click();
    const add = page.getByRole('button', { name: 'New analysis session' });
    const before = await page.getByRole('tab').count();
    for (let i = 0; i < 4; i += 1) {
      await add.click();
      await expect(page.getByRole('tab')).toHaveCount(before + i + 1);
    }

    const strip = page.getByRole('tablist', { name: 'Analysis sessions' });
    // Scrollable, not compressed: four tabs squeezed into 360 px would leave every label unreadable.
    const scrollable = await strip.evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(scrollable).toBe(true);
  });
});
