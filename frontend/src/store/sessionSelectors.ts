// store/sessionSelectors.ts — derived reads for the active session.
//
// Why selectors instead of flat fields
// -----------------------------------
// `useQuantStore` maintained `sessionsByKey` AND a ~12-field flat mirror of the active
// session, kept in step by `projectSession()` on every write. Three actions
// (`clearAiPlan`, `clearQa`, `askQuestion`) wrote only the flat copy, so the two diverged
// silently — and because the UI read the flat fields, the divergence was invisible until a
// session switch projected the stale archive back over it.
//
// Deriving from `sessions[activeSessionId]` removes the second copy entirely. There is one
// mutable source and one way to read it, so there is nothing left to keep in step.
//
// Every export is a plain function of the store state, so it can be passed straight to
// `useSessionStore(selector)` and memoises on reference equality — a component re-renders
// when ITS session changes, not when any session changes, which matters once several tabs
// are streaming at once.

import { blankSession, isActionableTrade, type AiExecutionPlan, type QaChatMessage, type QuantSession, type ReasoningStep } from './useQuantStore';
import {
  blankUi,
  useSessionStore,
  type SessionId,
  type SessionStream,
  type SessionUiState,
} from './useSessionStore';

type State = ReturnType<typeof useSessionStore.getState>;

// ── Stable empty values ──────────────────────────────────────────────────────
//
// The fallbacks below MUST be referentially stable, not freshly constructed.
//
// zustand's `useStore` is built on `useSyncExternalStore`, which calls the selector on every
// render and compares the result with `Object.is`. A selector that builds a new object each
// call therefore never compares equal, and React raises "The result of getSnapshot should be
// cached to avoid an infinite loop" — for the object/array-valued selectors below, an actual
// render loop.
//
// "No active session" is the INITIAL state of the multi-session path, so a fresh
// `blankSession()` fallback would fail on first paint of every migrated component rather than
// in some rare corner.
//
// Frozen because they are shared: a consumer that mutated one would corrupt the empty state
// for every other consumer, and freezing turns that into a visible error instead of a
// haunting. `blankSession()` itself is left alone — the store needs a fresh mutable object
// each time it creates a real session.
const EMPTY_SESSION: QuantSession = Object.freeze(blankSession()) as QuantSession;
const EMPTY_STREAM: SessionStream = Object.freeze({
  threadId: null,
  runId: null,
  lastSeq: 0,
  hydratedAt: null,
}) as SessionStream;
const EMPTY_UI: SessionUiState = Object.freeze(blankUi()) as SessionUiState;

/**
 * The active session, or a blank one.
 *
 * Never `null`, deliberately. Every consumer would otherwise need the same
 * `?? blankSession()` guard, and one of them would forget — the flat mirror existed partly
 * to spare components exactly this. A blank session renders as the empty state, which is
 * the honest thing to show when nothing is selected.
 *
 * Reference-stable in the empty case, which the field selectors below depend on.
 */
export function selectCurrentSession(state: State): QuantSession {
  const id = state.activeSessionId;
  if (!id) return EMPTY_SESSION;
  return state.sessions[id] ?? EMPTY_SESSION;
}

export function selectSession(sessionId: SessionId | null) {
  return (state: State): QuantSession => {
    if (!sessionId) return EMPTY_SESSION;
    return state.sessions[sessionId] ?? EMPTY_SESSION;
  };
}

export function selectCurrentStream(state: State): SessionStream {
  const id = state.activeSessionId;
  if (!id) return EMPTY_STREAM;
  return state.streams[id] ?? EMPTY_STREAM;
}

export function selectCurrentUi(state: State): SessionUiState {
  const id = state.activeSessionId;
  if (!id) return EMPTY_UI;
  return state.ui[id] ?? EMPTY_UI;
}

/**
 * The UI slice for a NAMED key, rather than for whichever session is active.
 *
 * Needed because the legacy path has no session ids at all: its UI state is parked under a
 * fixed key, so a reader resolving `activeSessionId` (which is `null` there) would always see
 * the empty slice and never observe its own writes.
 */
export function selectUiFor(key: string | null) {
  return (state: State): SessionUiState => {
    if (!key) return EMPTY_UI;
    return state.ui[key] ?? EMPTY_UI;
  };
}

// ── Field selectors ──────────────────────────────────────────────────────────
//
// Preferred over `selectCurrentSession` in components. Selecting a primitive means a tab
// re-renders only when the field it displays changes, rather than on every frame of every
// session — which is the difference between one streaming tab and eight being usable.

export const selectSessionStatus = (state: State): QuantSession['sessionStatus'] =>
  selectCurrentSession(state).sessionStatus;

export const selectIsAnalyzing = (state: State): boolean =>
  selectCurrentSession(state).isAnalyzing;

export const selectReasoningSteps = (state: State): ReasoningStep[] =>
  selectCurrentSession(state).reasoningSteps;

export const selectQaMessages = (state: State): QaChatMessage[] =>
  selectCurrentSession(state).qaMessages;

export const selectQaStatus = (state: State): QuantSession['qaStatus'] =>
  selectCurrentSession(state).qaStatus;

export const selectFinalTrade = (state: State): AiExecutionPlan | null =>
  selectCurrentSession(state).finalTrade;

export const selectAiPlan = (state: State): AiExecutionPlan | null =>
  selectCurrentSession(state).aiPlan;

export const selectAnalysisError = (state: State): string | null =>
  selectCurrentSession(state).analysisError;

/** The thread id for Q&A and cancel. Reads the ACTIVE session's stream, never a global. */
export const selectCurrentThreadId = (state: State): string | null =>
  selectCurrentStream(state).threadId;

export const selectCurrentRunId = (state: State): string | null =>
  selectCurrentStream(state).runId;

export const selectMode = (state: State): SessionUiState['mode'] => selectCurrentUi(state).mode;

export const selectDraft = (state: State): string => selectCurrentUi(state).draft;

export const selectVerification = (state: State): SessionUiState['verification'] =>
  selectCurrentUi(state).verification;

// ── Derived predicates ───────────────────────────────────────────────────────

/**
 * Whether the composer may send.
 *
 * Unlocks at `watching` or `complete` and only with a thread id, matching the existing
 * `TradeQaPanel` gate — the backend needs the thread to ground the answer, so offering the
 * control earlier would produce a failure the user cannot act on.
 *
 * The `qaStatus` check is now PER SESSION. It was a flat field, so a Q&A on one session
 * blocked a Q&A on every other one.
 */
export function selectCanAskQuestion(state: State): boolean {
  const session = selectCurrentSession(state);
  const stream = selectCurrentStream(state);
  const unlocked = session.sessionStatus === 'watching' || session.sessionStatus === 'complete';
  return unlocked && !!stream.threadId && session.qaStatus !== 'streaming';
}

/** Whether the active session has a committed, actionable trade plan to render. */
export function selectHasActionablePlan(state: State): boolean {
  const session = selectCurrentSession(state);
  return session.sessionStatus === 'complete' && isActionableTrade(session.finalTrade);
}

/**
 * Session ids that are currently streaming — what the tab bar shows an indicator on.
 *
 * Sorted for a deterministic order, and MEMOIZED for a stable reference.
 *
 * Sorting alone is not enough: it fixes the order of the elements but still returns a new
 * array on every call, so `useSessionStore(selectStreamingSessionIds)` would never compare
 * equal under `Object.is` and React would reject the uncached snapshot. The cache is keyed on
 * the `sessions` object identity, which the store replaces on every write, so the result is
 * recomputed exactly when it can have changed.
 *
 * A single-entry cache is enough because there is one sessions map per client.
 */
let streamingCache: { sessions: State['sessions']; ids: SessionId[] } | null = null;

export function selectStreamingSessionIds(state: State): SessionId[] {
  if (streamingCache && streamingCache.sessions === state.sessions) return streamingCache.ids;
  const ids = Object.entries(state.sessions)
    .filter(([, s]) => s.isAnalyzing || s.sessionStatus === 'running' || s.qaStatus === 'streaming')
    .map(([id]) => id)
    .sort();
  // Reuse the previous array when the CONTENTS are unchanged, so a frame that touches an
  // unrelated field of some session does not re-render the whole tab bar.
  const previous = streamingCache?.ids;
  const unchanged =
    previous && previous.length === ids.length && previous.every((id, i) => id === ids[i]);
  const result = unchanged ? previous : ids;
  streamingCache = { sessions: state.sessions, ids: result };
  return result;
}

/** Whether any session is streaming. For a global "the agent is working" affordance. */
export const selectAnyStreaming = (state: State): boolean =>
  selectStreamingSessionIds(state).length > 0;
