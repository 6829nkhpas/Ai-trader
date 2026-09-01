// lib/fq/rehydrate.ts — rebuild a session's client state from the server.
//
// The anti-drift rule
// -------------------
// A restored transcript is built by replaying the STORED SSE FRAMES through
// `applyStreamEvent` — the same reducer a live stream drives. There is deliberately no
// second renderer that turns stored rows into steps.
//
// That is the whole reason the backend stores frame payloads rather than renderings of
// them. A parallel "render from the database" path would start identical and drift on the
// first reducer change, and the divergence would show up as a reopened session looking
// subtly different from the one the user had watched — the hardest class of bug to even
// report, let alone diagnose.
//
// The honesty rule
// ----------------
// Replaying frames alone is NOT enough, and this is the subtle part. A run whose process
// died mid-stream has no terminal frame, so the reducer leaves the session `running` with
// `isAnalyzing: true` — i.e. a dead run rendering as "still analysing", forever. So after
// the replay the session status is reconciled against the RUN's stored status, which the
// server resolved honestly at startup (`reconcile_stale_runs`). A `truncated` run becomes a
// visible error explaining it was interrupted, never a spinner and never a plan.

import {
  applyStreamEvent,
  blankSession,
  type QaChatMessage,
  type QuantSession,
} from '../../store/useQuantStore';
import {
  getSession,
  listMessages,
  listRunEvents,
  listRuns,
  type SessionSummary,
  type StoredEvent,
  type StoredMessage,
  type StoredRun,
} from './api';

/** How far back a reopened session rebuilds its glass box. */
const MAX_REPLAY_EVENTS = 4000;

/** How many chat turns are loaded on open. */
const MAX_MESSAGES = 500;

export interface RehydratedSession {
  summary: SessionSummary;
  session: QuantSession;
  runs: StoredRun[];
  /** The run whose transcript was replayed, if any. */
  activeRun: StoredRun | null;
  /** Highest replayed `seq`, for a `?after_seq=` reattach. */
  lastSeq: number;
  /** True when the run is still producing, so the caller should attach to the stream. */
  isLive: boolean;
}

/**
 * Which run's glass box to show when a session is reopened.
 *
 * The session's `active_run_id` when it resolves, otherwise the newest run. A session with
 * several FIND runs shows the most recent by default; an earlier one is reachable
 * explicitly, which is what `context_run_id` exists for.
 */
export function pickActiveRun(summary: SessionSummary, runs: StoredRun[]): StoredRun | null {
  if (runs.length === 0) return null;
  if (summary.active_run_id) {
    const named = runs.find((r) => r.run_id === summary.active_run_id);
    if (named) return named;
  }
  return runs[runs.length - 1];
}

/** Whether a run is still producing frames. */
export function isRunLive(run: StoredRun | null): boolean {
  return !!run && (run.status === 'running' || run.status === 'watching');
}

/**
 * Replay stored frames through the live reducer.
 *
 * Ordered by `seq` before replay rather than trusting the response order. The API returns
 * them ordered, but the reducer's behaviour is order-dependent in ways that matter —
 * DECISION is first-write-wins, and REASONING coalesces into the trailing step — so a
 * transport that ever reordered would produce a plausible-looking wrong transcript rather
 * than an obvious failure.
 */
export function replayEvents(events: StoredEvent[], base?: QuantSession): QuantSession {
  const ordered = [...events].sort((a, b) => a.seq - b.seq);
  let session = base ?? blankSession();
  for (const event of ordered) {
    session = applyStreamEvent(session, { event: event.event, data: event.data });
  }
  return session;
}

/**
 * Turn stored chat rows into the Q&A transcript the terminal renders.
 *
 * `truncated` / `error` / `cancelled` answers are marked `error: true`. That flag is what
 * stops a half-received answer from rendering as a good one — the whole point of storing a
 * distinct status per message rather than just its text.
 *
 * A row still `streaming` on a freshly-loaded session means the run genuinely is producing,
 * so it keeps `streaming: true` and the live stream will finish it.
 */
export function toQaMessages(messages: StoredMessage[]): QaChatMessage[] {
  const out: QaChatMessage[] = [];
  for (const message of messages) {
    if (message.kind !== 'qa_question' && message.kind !== 'qa_answer') continue;
    const failed =
      message.status === 'truncated' || message.status === 'error' || message.status === 'cancelled';
    out.push({
      // A STILL-STREAMING assistant answer gets the same id the live path derives from the
      // frame (`qa-<run_id>`), so when the stream reattaches its chunks land on this turn
      // instead of creating a second, parallel bubble for the same answer. Finished turns keep
      // their stable `message_id`, which is the better key for a list that will not change.
      id:
        message.status === 'streaming' && message.role === 'assistant' && message.run_id
          ? `qa-${message.run_id}`
          : message.message_id,
      role: message.role === 'user' ? 'user' : 'assistant',
      content: message.content || (failed ? describeFailedMessage(message) : ''),
      activity: message.activity ?? undefined,
      streaming: message.status === 'streaming',
      error: failed || undefined,
    });
  }
  return out;
}

/**
 * What to show for an answer with no text.
 *
 * An empty bubble is worse than an explanation: the user can see something went wrong but
 * not what, and cannot tell it apart from a still-loading turn.
 */
function describeFailedMessage(message: StoredMessage): string {
  if (message.error_detail) return message.error_detail;
  if (message.status === 'cancelled') return 'This answer was cancelled.';
  if (message.status === 'truncated') {
    return 'This answer was interrupted before it finished. Ask again to get a complete reply.';
  }
  return 'This answer could not be completed.';
}

/**
 * Reconcile a replayed session against the run's stored status.
 *
 * THE honesty step. Frame replay alone leaves a run that died mid-stream looking `running`
 * with `isAnalyzing: true` — a dead run rendering as a live one indefinitely. The server
 * already resolved what actually happened (`reconcile_stale_runs` consults the durable
 * checkpoint to distinguish "genuinely resumable" from "the process died"), so its verdict
 * is authoritative here.
 *
 * `truncated` maps onto the existing `error` status rather than widening
 * `sessionStatus`. Adding a sixth state would ripple through every component and the four
 * reducer property suites for no gain — what the user needs is to be told the analysis did
 * not finish, and the error path already says things clearly.
 */
export function reconcileWithRun(session: QuantSession, run: StoredRun | null): QuantSession {
  if (!run) return session;

  switch (run.status) {
    case 'running':
      return { ...session, sessionStatus: 'running', isAnalyzing: true };
    case 'watching':
      // Resumable: the watcher will wake it, and the composer should be unlocked.
      return { ...session, sessionStatus: 'watching', isAnalyzing: false };
    case 'complete':
      return { ...session, sessionStatus: 'complete', isAnalyzing: false };
    case 'cancelled':
      return {
        ...session,
        sessionStatus: 'idle',
        isAnalyzing: false,
        analysisError: null,
      };
    case 'error':
      return {
        ...session,
        sessionStatus: 'error',
        isAnalyzing: false,
        analysisError:
          session.analysisError ?? 'This analysis failed before it produced a result.',
      };
    case 'truncated':
      return {
        ...session,
        sessionStatus: 'error',
        isAnalyzing: false,
        // Never a spinner and never a plan. The run stopped without finishing, and the
        // partial transcript above it is what it got to.
        analysisError:
          session.analysisError ??
          'This analysis was interrupted before it finished, so its conclusion is incomplete. ' +
            'Run it again for a current read.',
        // A truncated run may have emitted a DECISION before dying. Keeping it would render
        // an executable trade card for an analysis that never completed its own
        // verification, so the plan is dropped while the reasoning is kept.
        finalTrade: null,
        aiPlan: null,
      };
    default:
      return session;
  }
}

/**
 * Load everything needed to display a session, and rebuild its client state.
 *
 * Throws `FqApiError` — the caller distinguishes 404 (gone: show not-found and offer a new
 * session) from 401 (the cookie expired: the app shell re-checks auth). Collapsing them
 * would make an expired login look like a deleted conversation.
 */
export async function rehydrateSession(sessionId: string): Promise<RehydratedSession> {
  // The summary first and on its own: if the session is gone or not ours, there is no point
  // fetching three more collections to be told the same thing.
  const summary = await getSession(sessionId);

  const [{ items: runs }, { items: messages }] = await Promise.all([
    listRuns(sessionId),
    listMessages(sessionId, { limit: MAX_MESSAGES }),
  ]);

  const activeRun = pickActiveRun(summary, runs);

  let session = blankSession();
  let lastSeq = 0;

  if (activeRun) {
    const { items: events, last_seq } = await listRunEvents(activeRun.run_id, {
      limit: MAX_REPLAY_EVENTS,
    });
    session = replayEvents(events);
    // The RUN's `last_seq`, not the page's: a transcript longer than the replay cap must
    // still reattach at the true high-water mark or the gap request would re-deliver frames
    // that were merely not loaded.
    lastSeq = Math.max(last_seq, ...events.map((e) => e.seq), 0);
  }

  session = reconcileWithRun(session, activeRun);
  session = {
    ...session,
    currentThreadId: activeRun?.thread_id ?? null,
    qaMessages: toQaMessages(messages),
    // `mode` is per-run, and what the UI should show is the run being displayed.
    mode: activeRun?.kind === 'verify' ? 'VERIFY' : 'FIND',
    updatedAt: summary.updated_at * 1000,
  };

  return {
    summary,
    session,
    runs,
    activeRun,
    lastSeq,
    isLive: isRunLive(activeRun),
  };
}
