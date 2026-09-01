// tests/fq-multi-session.spec.ts
//
// The end-to-end journey the migration exists to make possible:
//
//   create a session -> FIND -> streamed result -> ask a follow-up ->
//   create a SECOND session -> switch -> assert isolation -> reload -> restore ->
//   open history -> reopen an archived session
//
// This replaces `tests/e2e.spec.ts`, which drove `invoke('run_deep_quant_analysis')` through a
// stubbed Tauri IPC bridge and cited `src-tauri/src/quant/mod.rs`. The desktop shell is gone and that
// file does not exist, so the old spec tested an architecture that no longer ships.
//
// WHAT IS REAL AND WHAT IS STUBBED
// --------------------------------
// Real: the production Next build, the same-origin `/api/deepquant/*` proxy, the HMAC identity chain
// (cookie -> /users/me -> assertion -> server-side verification), the FastAPI service, the SQLite
// session store, write-through stream persistence, ownership checks, and the SSE assembler.
//
// Stubbed: the LangGraph graph (canned frames — see `agents/deep-quant-loop/e2e_stub_server.py`) and
// the auth API's `/users/me` (see `tests/support/stub-identity.mjs`).
//
// So this proves the session/streaming/isolation/persistence WIRING. It proves nothing about model
// quality, prompt behaviour, market data, or the real price-trigger watcher. Those stay manual.

import { test, expect, type Page } from '@playwright/test';

const ALICE = 'e2e-alice-token';

/**
 * Unique per RUN, so a second run of the same test does not inherit the first run's sessions.
 *
 * `tokenForTest` derives a user from the test TITLE, which isolates tests from each other but is stable
 * across runs — so re-running the suite against a still-running agent had each test find its own leftovers
 * and count them as tabs. That made local reruns non-reproducible, and it is the residual cause of the
 * flakiness this suite showed (4/1, then 2/3, then 3/2, with no code changing).
 *
 * Read from the environment when provided, so CI can pin it in the log for a reproducible rerun.
 */
const RUN_NONCE = process.env.E2E_RUN_ID ?? Date.now().toString(36);

/**
 * Authenticate by planting the cookie the identity chain reads.
 *
 * Not a bypass: `app/api/_identity.ts` still exchanges this for a user at `/users/me` and mints a
 * real HMAC assertion, which deep-quant still verifies. Only the cookie's ISSUER is faked.
 */
/**
 * A token unique to the running test, so each test gets its OWN user.
 *
 * Sessions are per-user and the agent's database lives for the whole Playwright run, so tests sharing an
 * identity share a session list: the tab bar counts leftovers from earlier tests and assertions fail with
 * "locator resolved to 7 elements". Isolating by identity is cheaper than restarting the service per test
 * and removes the coupling outright.
 */
function tokenForTest(suffix = ''): string {
  const slug = test
    .info()
    .title.toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .slice(0, 40);
  return `e2e-${RUN_NONCE}-${slug}${suffix}`;
}

async function signIn(page: Page, token = ALICE) {
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

/** Every visible session tab, by its accessible name. */
async function tabNames(page: Page): Promise<string[]> {
  return page.getByRole('tab').evaluateAll((els) =>
    els.map((el) => el.getAttribute('aria-label') ?? el.textContent ?? ''),
  );
}

/**
 * Open the AI Agent panel, which is where `DeepQuantPanel` — and therefore the session tab bar —
 * actually lives.
 *
 * `RightSidebar` renders it only when `sidebarTab === 'deepquant'`, reached from the right-hand rail.
 * "Quant Radar" in the primary nav was tried first and is a DIFFERENT panel; the suite then failed
 * against a page snapshot of a fully authenticated terminal, which reads as a broken tab bar rather
 * than a panel that was never opened.
 */
async function openAgentPanel(page: Page) {
  await page.getByRole('button', { name: 'Show AI Agent panel' }).click();
  await expect(newSessionButton(page)).toBeVisible();
}

/** The `session_id` of the tab at `index`, read from the wrapper the bar stamps it on. */
async function sessionIdOfTab(page: Page, index: number): Promise<string> {
  return page
    .locator('[data-session-id]')
    .nth(index)
    .evaluate((el) => el.getAttribute('data-session-id') ?? '');
}

/** The tab for a NAMED session, independent of where the bar currently orders it. */
function tabFor(page: Page, sessionId: string) {
  return page.locator(`[data-session-id="${sessionId}"] [role="tab"]`);
}

/** The tab bar's own new-session control. */
function newSessionButton(page: Page) {
  return page.getByRole('button', { name: 'New analysis session' });
}

/** Start an analysis in the session that is currently on screen. */
async function runFind(page: Page) {
  await page.locator('#btn-run-deep-quant').click();
}

/**
 * Expand EVERY collapsed "Thinking" group.
 *
 * `ThinkingGroupRenderer` opens only while a run is live, so a finished run — restored, or simply
 * switched back to — hides its reasoning behind a closed toggle. That is correct product behaviour.
 *
 * All of them, not `.first()`: the transcript groups CONSECUTIVE message steps, and a tool call breaks
 * the group. The canned script reasons, calls `get_ohlc`, then reasons again, so "Scanning RELIANCE" and
 * "Momentum is intact" land in DIFFERENT groups — expanding only the first left the second hidden and
 * the failure read as "the frames never arrived".
 */
async function expandAllThinking(page: Page) {
  // POLLED, not counted once. `locator.count()` resolves immediately, so on a freshly opened session it
  // returned 0 before the replayed transcript had rendered — which read like a render bug rather than a
  // race.
  //
  // Waits for AT LEAST one group and expands whatever is present, rather than demanding a specific count.
  // A stricter "exactly two" check was tried as a diagnostic and is wrong here: how many groups a
  // transcript renders depends on where tool calls fall in it, so a shared helper asserting a count makes
  // every caller depend on the fixture's shape. The thing under test is whether the post-tool reasoning is
  // READABLE, which each caller asserts for itself.
  await expect
    .poll(() => page.getByRole('button', { name: /Thinking/ }).count(), {
      message: 'the transcript never rendered a Thinking group',
      timeout: 30_000,
    })
    .toBeGreaterThan(0);

  const total = await page.getByRole('button', { name: /Thinking/ }).count();

  for (let i = 0; i < total; i += 1) {
    // Re-queried each iteration: clicking re-renders the transcript, so a locator captured up front can
    // go stale and silently resolve to the wrong element.
    const toggle = page.getByRole('button', { name: /Thinking/ }).nth(i);
    if ((await toggle.getAttribute('aria-expanded')) === 'true') continue;
    await toggle.click();
    // Verified, not fired-and-forgotten. Without this a click that lands on the wrong element leaves the
    // group closed and the failure surfaces as missing text three lines later.
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  }
}

/**
 * Wait for the streamed run to reach its terminal state.
 *
 * Asserted through the COMPOSER unlocking rather than a status string. The run-state text
 * ("Complete" / "Watching") lives in `SessionWorkspace`'s header, which only exists on the standalone
 * route — looking for it in the side panel waits 60s for an element that is never rendered there.
 *
 * The composer is also the better signal: it unlocks only at `watching`/`complete` AND once a thread id
 * is bound, so it proves the terminal frame arrived and was routed to this session, not merely that
 * some text appeared.
 */
async function waitForComplete(page: Page) {
  await expect(page.getByRole('textbox').first()).toBeEnabled({ timeout: 60_000 });
}

test.describe('Find Quant multi-session workspace', () => {
  /** Every `/api/deepquant/*` call the browser made, with its status. Reset per test. */
  const agentCalls: string[] = [];

  test.beforeEach(async ({ page }) => {
    await signIn(page, tokenForTest());
    // Surface anything the app throws. A silent uncaught exception is how a broken store update
    // presents in a browser, and without this the test would only see a missing element.
    page.on('pageerror', (err) => {
      throw new Error(`uncaught page error: ${err.message}`);
    });

    // Record every agent call and its status.
    //
    // Added because a failure here is otherwise unreadable: the suite reported "element not found"
    // while the server database was empty, and nothing in the output said whether `POST /sessions` had
    // been refused, never sent, or sent and lost. A missing element is a symptom; the HTTP exchange is
    // the cause, and it belongs in the failure output rather than in a separate debugging session.
    page.on('response', (res) => {
      const url = res.url();
      if (!url.includes('/api/deepquant/')) return;
      const line = `${res.status()} ${res.request().method()} ${url.replace(/^https?:\/\/[^/]+/, '')}`;
      agentCalls.push(line);
      // Non-2xx is reported immediately: a 401 here means the identity chain broke, which is a very
      // different bug from a selector that does not match.
      if (res.status() >= 400) console.log(`[e2e] agent call failed: ${line}`);
    });
  });

  test.afterEach(async () => {
    // UNCONDITIONAL, and attached rather than logged.
    //
    // The first version only printed when `test.info().status !== expectedStatus`, and it printed
    // nothing at all on a failing run — which made "no agent calls were recorded" look like evidence
    // that the browser never called the agent, when in fact the recorder's own output was missing.
    // A diagnostic that can silently produce nothing is worse than none, because it invites exactly
    // that wrong conclusion.
    //
    // `attach` puts it in the HTML report and the trace, where it survives a truncated terminal.
    const body = agentCalls.length ? agentCalls.join('\n') : '(no /api/deepquant/* calls observed)';
    await test.info().attach('agent-calls', { body, contentType: 'text/plain' });
    console.log(`[e2e] agent calls (${agentCalls.length}):\n  ${agentCalls.join('\n  ') || '(none)'}`);
    agentCalls.length = 0;
  });

  test('the whole journey, on one page', async ({ page }) => {
    await page.goto('/');
    await openAgentPanel(page);

    // ── create ────────────────────────────────────────────────────────────────
    //
    // Counted RELATIVELY, not asserted as 1. The tab bar lists every active session the user owns, so
    // an absolute count only holds against an empty database — and when it failed it read as a broken
    // tab bar rather than as leftovers from an earlier run. The stub server now wipes its state on
    // boot as well; both guards, because either alone leaves the suite order-dependent.
    const before = await page.getByRole('tab').count();
    await newSessionButton(page).click();

    // Asserted BEFORE the tab count, so a refused or absent request names itself instead of surfacing
    // three steps later as a missing frame. `POST /sessions` is the one call whose absence explains
    // every downstream symptom, and the server's answer is what says which of the two happened.
    await expect
      .poll(() => agentCalls.filter((c) => c.includes('POST /api/deepquant/sessions')).length, {
        message: `no POST /sessions was observed. Calls seen:\n  ${agentCalls.join('\n  ') || '(none)'}`,
      })
      .toBeGreaterThan(0);
    const created = agentCalls.find((c) => c.includes('POST /api/deepquant/sessions')) ?? '';
    expect(created, `POST /sessions was refused: ${created}`).toMatch(/^201 /);

    await expect(page.getByRole('tab')).toHaveCount(before + 1);
    const names = await tabNames(page);
    expect(names.length).toBe(before + 1);
    expect(names[names.length - 1]).toBeTruthy();

    // The id is captured because TAB ORDER IS NOT STABLE: the list is ordered by `updated_at DESC`, so
    // running the second session moves it to the front. Clicking `nth(0)` therefore selected the WRONG
    // session — and the isolation assertion still passed, because both sessions stream the same canned
    // script. It only failed on the Q&A turn, which exists in one of them. Positional selection would
    // have kept quietly proving nothing.
    const firstId = await sessionIdOfTab(page, names.length - 1);
    expect(firstId).not.toBe('');

    // ── FIND, streamed ────────────────────────────────────────────────────────
    await runFind(page);
    // The opening frames must arrive. The panel-level listener exists precisely because
    // `AgentTerminal` mounts late and used to miss them.
    await expect(page.getByText(/Scanning RELIANCE/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/Momentum is intact/)).toBeVisible();
    await waitForComplete(page);

    // ── ask a follow-up ───────────────────────────────────────────────────────
    const composer = page.getByRole('textbox').first();
    await expect(composer).toBeEnabled();
    await composer.fill('why is the stop there?');
    await composer.press('Enter');
    // The question appears optimistically, then the answer streams into the CHAT.
    await expect(page.getByText('why is the stop there?')).toBeVisible();
    await expect(page.getByText(/swing low at 2,435/)).toBeVisible({ timeout: 30_000 });

    // ── a second session ──────────────────────────────────────────────────────
    await newSessionButton(page).click();
    await expect(page.getByRole('tab')).toHaveCount(before + 2);

    // The new session is empty. Under the old flat mirror the first session's transcript was
    // projected over whatever was on screen, so this is the assertion that would have failed.
    await expect(page.getByText(/Scanning RELIANCE/)).toHaveCount(0);

    // ── run in the second, then switch back mid-flight ────────────────────────
    await runFind(page);
    await tabFor(page, firstId).click();

    // Session one still shows ITS OWN finished analysis while session two streams in the
    // background. This is the isolation the whole migration is for.
    await expandAllThinking(page);
    await expect(page.getByText(/Momentum is intact/)).toBeVisible();
    await expect(page.getByText('why is the stop there?')).toBeVisible();

    // ── reload -> restore ─────────────────────────────────────────────────────
    await page.reload();
    await expect(page.getByRole('tab')).toHaveCount(before + 2);
    await tabFor(page, firstId).click();
    // Nothing is in memory after a reload: every word here came back from the stored frames.
    await expandAllThinking(page);
    await expect(page.getByText(/Momentum is intact/)).toBeVisible({ timeout: 30_000 });
    // And the Q&A turn is restored as a CHAT turn, not as glass-box reasoning — the `turn` marker
    // is what makes the live and restored views agree.
    await expect(page.getByText('why is the stop there?')).toBeVisible();

    // ── history -> archive -> reopen ──────────────────────────────────────────
    await page.getByRole('button', { name: 'Session history' }).click();
    const history = page.getByRole('list', { name: 'Session history' });
    await expect(history).toBeVisible();

    await history.getByRole('button', { name: /^Archive/ }).first().click();
    await expect(page.getByRole('tab')).toHaveCount(before + 1);
  });

  test('a session belonging to someone else is not found', async ({ page, context }) => {
    // Ownership, proven rather than assumed. Alice creates a session; Bob asks for it by id.
    await page.goto('/');
    await openAgentPanel(page);
    const startCount = await page.getByRole('tab').count();
    await newSessionButton(page).click();
    await expect(page.getByRole('tab')).toHaveCount(startCount + 1);

    // Read the id straight off the tab, which is the only place the client holds it.
    const sessionId = await page.getByRole('tab').last().evaluate((el) => {
      return el.closest('[data-session-id]')?.getAttribute('data-session-id') ?? '';
    });
    expect(sessionId).not.toBe('');

    // Become Bob.
    await context.clearCookies();
    await signIn(page, tokenForTest('-bob'));
    await page.goto(`/find-trade/session/${sessionId}`);

    // 404, NOT 403. A 403 would confirm the id is real, turning the route into an enumeration
    // oracle — and the wording must not leak "not yours" either.
    await expect(page.getByText(/isn.t available/i)).toBeVisible();
    await expect(page.getByText(/permission|forbidden|not yours/i)).toHaveCount(0);
  });

  test('a deep link restores a session with nothing in memory', async ({ page }) => {
    await page.goto('/');
    await openAgentPanel(page);
    await newSessionButton(page).click();
    await runFind(page);
    await waitForComplete(page);

    const sessionId = await page.getByRole('tab').last().evaluate((el) => {
      return el.closest('[data-session-id]')?.getAttribute('data-session-id') ?? '';
    });

    // A brand-new context: no store, no cache, nothing but the URL and the cookie.
    await page.goto(`/find-trade/session/${sessionId}`);

    await expect(page.getByRole('heading', { level: 1 })).toContainText(/RELIANCE/);
    // A finished run renders its reasoning collapsed, which is correct product behaviour — so the
    // group is expanded to prove the frames really came back rather than asserting on the closed
    // summary.
    await expandAllThinking(page);
    await expect(page.getByText(/Momentum is intact/)).toBeVisible();
  });
});
