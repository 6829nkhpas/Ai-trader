// store/useSessionStore.ts — per-session state for the Find Quant Trade workspace.
//
// What this replaces, and why it is a separate store
// --------------------------------------------------
// `useQuantStore` keyed sessions by `${SYMBOL}::${PROFILE}` and mirrored the active one
// into ~12 flat top-level fields. Four defects followed from that shape, all verified in
// the code before this was written:
//
//   1. The key COLLIDES. A second FIND on RELIANCE/INTRADAY overwrote the first, and two
//      timeframes for one symbol could not coexist at all.
//   2. Event routing FELL BACK to the active session. Both the `RUN_STARTED` branch and
//      the `else` branch resolved `runKey = _streamingKey || activeViewKey`, so a frame
//      whose thread was unknown landed wherever the user happened to be looking.
//   3. Q&A routed by REACT CLOSURE into the flat `qaMessages` array, and never wrote
//      `sessionsByKey` at all. Switching session mid-answer projected a different array
//      over the flat field and every subsequent chunk was silently dropped.
//   4. `qaStatus` was a flat field, so a Q&A on one session blocked a Q&A on another —
//      process-wide, not per session.
//
// The invariant this store exists to hold
// ---------------------------------------
// **A frame belonging to session A can never modify session B.** There is no fallback.
// An unroutable frame mutates nothing and is counted, because "route it somewhere" is
// what produced defect 2 and silently corrupting a transcript is worse than dropping a
// frame you can see the count of.
//
// That is safe to enforce because the backend stamps `thread_id` on EVERY payload
// (`main.py` `_run_events`), including Q&A frames — the old client simply ignored it.
//
// Division of labour with `useQuantStore`
// --------------------------------------
// `useQuantStore` keeps the reducer (`applyStreamEvent`), the trade-plan extractors, and
// the unrelated consensus / sentiment / patterns slices. This store owns which session a
// frame belongs to, the per-session ephemeral UI state, and the thread→session map. The
// reducer is IMPORTED, not reimplemented: four property-test suites pin behaviour on it
// that a second copy would drift from.

import { create } from 'zustand';

import {
  applyStreamEvent,
  blankSession,
  type QuantSession,
  type StreamEventPayload,
} from './useQuantStore';

/** Opaque, server-minted session id (`sess_…`). Never composed from symbol/profile. */
export type SessionId = string;

/** The FIND/VERIFY toggle. Was React state in `DeepQuantPanel`, i.e. global. */
export type QuantMode = 'FIND' | 'VERIFY';

/**
 * The VERIFY form, per session.
 *
 * Previously `useVerificationForm`, which reset on SYMBOL change only — so switching
 * session kept another session's entry/stop/target on screen, and a restored VERIFY
 * session could not show what had actually been verified.
 */
export interface VerificationDraft {
  side: string;
  entry: string;
  stopLoss: string;
  takeProfit: string;
  userAnalysis: string;
  hasManuallySetEntry: boolean;
  hasManuallySetSL: boolean;
  hasManuallySetTP: boolean;
}

/**
 * Ephemeral per-session UI state. Deliberately NOT persisted server-side.
 *
 * An unsent draft and a radio-button position are not conversation history; storing them
 * would make every keystroke a write. They only have to survive a tab switch, which is
 * what keeping them keyed by session id achieves.
 */
export interface SessionUiState {
  mode: QuantMode;
  draft: string;
  verification: VerificationDraft;
}

/** The live stream binding for a session. */
export interface SessionStream {
  threadId: string | null;
  runId: string | null;
  /** Highest `seq` seen, for `GET /stream/{thread}?after_seq=` gap recovery. */
  lastSeq: number;
  /**
   * When this session's state was rebuilt from the server, or `null` if it never was.
   *
   * The marker lives here rather than being inferred from `sessions[id]`, because
   * `setActiveSession` and `upsertSession` both create a BLANK session as a side effect — so the
   * existence of a session object says nothing about whether it was ever loaded. Without an explicit
   * marker, switching to a never-opened session shows an empty transcript that looks like a finished
   * conversation with nothing in it.
   */
  hydratedAt: number | null;
}

export function blankVerification(): VerificationDraft {
  return {
    side: 'BUY',
    entry: '',
    stopLoss: '',
    takeProfit: '',
    userAnalysis: '',
    hasManuallySetEntry: false,
    hasManuallySetSL: false,
    hasManuallySetTP: false,
  };
}

export function blankUi(): SessionUiState {
  return { mode: 'FIND', draft: '', verification: blankVerification() };
}

function blankStream(): SessionStream {
  return { threadId: null, runId: null, lastSeq: 0, hydratedAt: null };
}

/** Stable id for the assistant turn a Q&A run is streaming into. */
function qaTurnId(threadId: string, runId: string | null): string {
  // Keyed by the RUN, not by a timestamp or a random value. The previous implementation
  // matched chunks against an id captured in a React closure, which is why switching session
  // mid-answer silently dropped the rest of it: the closure outlived the state it referred
  // to. A deterministic id means any chunk can find its turn from the frame alone, with no
  // ambient state at all.
  return `qa-${runId ?? threadId}`;
}

/**
 * Fold one Q&A frame into a session's CHAT transcript.
 *
 * Separate from `applyStreamEvent` rather than a branch inside it, because the two write to
 * different places: `applyStreamEvent` owns `reasoningSteps` and has four property suites
 * pinning that behaviour, while this owns `qaMessages`. Mixing them would put the reducer's
 * guarantees at risk for an unrelated feature.
 *
 * Mirrors the event handling the old `askQuestion` listener performed, with the closure
 * replaced by a frame-derived id:
 *   REASONING / TEXT_MESSAGE -> append to the answer
 *   TOOL_CALL_START / _END   -> append an activity line
 *   RUN_FINISHED             -> stop streaming (idempotent)
 *   ERROR                    -> mark the turn failed
 */
function applyQaFrame(session: QuantSession, payload: StreamEventPayload): QuantSession {
  const data = payload.data;
  const threadId = typeof data?.thread_id === 'string' ? data.thread_id : '';
  const runId = typeof data?.run_id === 'string' ? data.run_id : null;
  const id = qaTurnId(threadId, runId);

  const messages = [...session.qaMessages];
  let index = messages.findIndex((m) => m.id === id);
  if (index === -1) {
    // The assistant turn is created on demand. A client that missed the optimistic insert —
    // a reattach, or a resumed Q&A — still renders the answer rather than dropping it.
    messages.push({ id, role: 'assistant', content: '', activity: [], streaming: true });
    index = messages.length - 1;
  }
  const turn = messages[index];

  switch (payload.event) {
    case 'RUN_STARTED':
      return session;
    case 'REASONING':
    case 'TEXT_MESSAGE': {
      const content = typeof data?.content === 'string' ? data.content : '';
      if (!content) return session;
      messages[index] = { ...turn, content: turn.content + content };
      break;
    }
    case 'TOOL_CALL_START': {
      const tool = typeof data?.tool === 'string' ? data.tool : '';
      if (!tool) return session;
      messages[index] = { ...turn, activity: [...(turn.activity ?? []), `> ${tool}…`] };
      break;
    }
    case 'TOOL_CALL_END': {
      const tool = typeof data?.tool === 'string' ? data.tool : '';
      if (!tool) return session;
      messages[index] = { ...turn, activity: [...(turn.activity ?? []), tool] };
      break;
    }
    case 'RUN_FINISHED':
      // Idempotent: a reattach can replay this, and flipping `streaming` back on would leave
      // the composer locked forever.
      messages[index] = { ...turn, streaming: false };
      return { ...session, qaMessages: messages, qaStatus: 'idle', updatedAt: Date.now() };
    case 'ERROR': {
      const error = typeof data?.error === 'string' ? data.error : 'Unknown Q&A error';
      messages[index] = {
        ...turn,
        // Keep whatever streamed; an empty bubble is worse than a partial answer plus the
        // reason it stopped.
        content: turn.content || error,
        error: true,
        streaming: false,
      };
      return { ...session, qaMessages: messages, qaStatus: 'idle', updatedAt: Date.now() };
    }
    default:
      return session;
  }

  return { ...session, qaMessages: messages, qaStatus: 'streaming', updatedAt: Date.now() };
}

interface SessionStore {
  /** Live session state, keyed by OPAQUE server id. */
  sessions: Record<SessionId, QuantSession>;
  /** Per-session stream binding. */
  streams: Record<SessionId, SessionStream>;
  /** Per-session ephemeral UI state. */
  ui: Record<SessionId, SessionUiState>;
  /**
   * `thread_id` → `session_id`. The ONLY routing mechanism.
   *
   * Populated when a run starts (from the `POST /run` response or the `RUN_STARTED`
   * frame's additive `session_id`), and on rehydration from `GET /sessions/{id}/runs`.
   */
  threadToSession: Record<string, SessionId>;
  /** Which session the workspace is showing. The only "which one" state in the system. */
  activeSessionId: SessionId | null;
  /**
   * Frames that could not be routed.
   *
   * Counted rather than silently swallowed: a non-zero value means a run is streaming
   * whose thread this client never bound, which is a real bug — and under the old
   * fallback it was invisible because such frames were written into the active session.
   */
  unroutableFrames: number;

  setActiveSession: (sessionId: SessionId | null) => void;
  upsertSession: (sessionId: SessionId, session?: Partial<QuantSession>) => void;
  replaceSession: (sessionId: SessionId, session: QuantSession) => void;
  bindThread: (threadId: string, sessionId: SessionId, runId?: string | null) => void;
  markHydrated: (sessionId: SessionId, lastSeq?: number) => void;
  applyFrame: (payload: StreamEventPayload) => SessionId | null;
  setUi: (sessionId: SessionId, patch: Partial<SessionUiState>) => void;
  setVerification: (sessionId: SessionId, patch: Partial<VerificationDraft>) => void;
  dropSession: (sessionId: SessionId) => void;
  reset: () => void;
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessions: {},
  streams: {},
  ui: {},
  threadToSession: {},
  activeSessionId: null,
  unroutableFrames: 0,

  /**
   * Switch the workspace to a session.
   *
   * No snapshot/restore step, unlike `activateSymbolSession`. There is nothing to
   * snapshot because there is no flat mirror: the session state was never copied out of
   * `sessions[id]` in the first place, so switching is a pointer move and a background
   * run keeps accumulating into its own entry regardless of what is on screen.
   */
  setActiveSession: (sessionId) => {
    if (sessionId === null) {
      set({ activeSessionId: null });
      return;
    }
    set((state) => ({
      activeSessionId: sessionId,
      sessions: state.sessions[sessionId]
        ? state.sessions
        : { ...state.sessions, [sessionId]: blankSession() },
      streams: state.streams[sessionId]
        ? state.streams
        : { ...state.streams, [sessionId]: blankStream() },
      ui: state.ui[sessionId] ? state.ui : { ...state.ui, [sessionId]: blankUi() },
    }));
  },

  /** Ensure a session entry exists, optionally merging fields into it. */
  upsertSession: (sessionId, session) => {
    set((state) => ({
      sessions: {
        ...state.sessions,
        [sessionId]: { ...(state.sessions[sessionId] ?? blankSession()), ...(session ?? {}) },
      },
      streams: state.streams[sessionId]
        ? state.streams
        : { ...state.streams, [sessionId]: blankStream() },
      ui: state.ui[sessionId] ? state.ui : { ...state.ui, [sessionId]: blankUi() },
    }));
  },

  /** Replace a session wholesale. Used by rehydration, which rebuilds from stored frames. */
  replaceSession: (sessionId, session) => {
    set((state) => ({
      sessions: { ...state.sessions, [sessionId]: session },
      streams: state.streams[sessionId]
        ? state.streams
        : { ...state.streams, [sessionId]: blankStream() },
      ui: state.ui[sessionId] ? state.ui : { ...state.ui, [sessionId]: blankUi() },
    }));
  },

  /**
   * Bind a thread to a session so its frames can be routed.
   *
   * Must happen before the first frame arrives, which is why `POST /run` returns the ids
   * synchronously AND `RUN_STARTED` carries them: either path binds, and whichever lands
   * first wins. Idempotent.
   */
  bindThread: (threadId, sessionId, runId) => {
    if (!threadId || !sessionId) return;
    set((state) => ({
      threadToSession: { ...state.threadToSession, [threadId]: sessionId },
      streams: {
        ...state.streams,
        [sessionId]: {
          ...(state.streams[sessionId] ?? blankStream()),
          threadId,
          runId: runId ?? state.streams[sessionId]?.runId ?? null,
        },
      },
    }));
  },

  /**
   * Record that a session's state was rebuilt from the server.
   *
   * `lastSeq` is taken as a FLOOR, never a reset. A frame can arrive between the rehydration
   * snapshot being taken and this call landing, and lowering the high-water mark would make the next
   * reattach ask for a gap it already has — re-delivering frames and duplicating the transcript.
   */
  markHydrated: (sessionId, lastSeq = 0) => {
    if (!sessionId) return;
    set((state) => ({
      streams: {
        ...state.streams,
        [sessionId]: {
          ...(state.streams[sessionId] ?? blankStream()),
          lastSeq: Math.max(state.streams[sessionId]?.lastSeq ?? 0, lastSeq),
          hydratedAt: Date.now(),
        },
      },
    }));
  },

  /**
   * Route one SSE frame to its session and apply the reducer. Returns the session, or
   * `null` when the frame could not be routed.
   *
   * THE HARD INVARIANT. Routing is `thread_id → session_id` and nothing else. There is no
   * `|| activeSessionId` fallback, and adding one back would reintroduce the defect this
   * store exists to remove: a frame from a background run landing in whatever the user is
   * looking at, corrupting a transcript in a way nobody can see.
   *
   * `RUN_STARTED` may carry `session_id` (additive, added by the backend in the same
   * migration), which lets a frame bind its own thread — so a client that missed the
   * `POST /run` response still routes correctly instead of dropping the whole run.
   */
  applyFrame: (payload) => {
    if (!payload || !payload.event) return null;
    const data = payload.data;
    const threadId = typeof data?.thread_id === 'string' ? data.thread_id : '';

    const state = get();
    let sessionId: SessionId | null = threadId ? state.threadToSession[threadId] ?? null : null;

    // A frame that NAMES its session is trusted.
    //
    // `RUN_STARTED` carries `session_id`/`run_id` from the server, so a client that never saw
    // the `POST /run` response can still attach the run. But it is not the only frame that
    // needs this: the bridge synthesizes terminal frames locally when a stream ends without
    // `RUN_FINISHED`, and error frames are built from a caught exception with no thread in
    // scope at all. Restricting self-binding to `RUN_STARTED` left those unroutable, which
    // meant a Q&A whose stream died left the composer locked with nothing able to unlock it.
    //
    // Accepting `session_id` on any frame is no weaker than accepting `thread_id`: both are
    // server-asserted, and a client-synthesized frame is local by construction.
    if (!sessionId) {
      const carried = typeof data?.session_id === 'string' ? data.session_id : '';
      if (carried) {
        // Only bind the thread when there IS one. A synthetic frame legitimately has no
        // thread id, and writing an empty key would poison the routing table.
        if (threadId) {
          get().bindThread(threadId, carried, typeof data?.run_id === 'string' ? data.run_id : null);
        }
        sessionId = carried;
      }
    }

    if (!sessionId) {
      // Dropped, and counted. Under the old fallback this frame would have been written
      // into whichever session was on screen, which is precisely the corruption being
      // prevented — and it was invisible, because it looked like data.
      set((s) => ({ unroutableFrames: s.unroutableFrames + 1 }));
      return null;
    }

    const current = state.sessions[sessionId] ?? blankSession();
    // A Q&A turn is a CHAT turn, not glass-box reasoning.
    //
    // The backend answers a follow-up on the analysis thread (that is how it stays grounded)
    // and the answer arrives as ordinary REASONING frames, so without the `turn` marker the
    // reducer would append the reply to the transcript. Rehydration reads the stored
    // `qa_answer` message rows and shows the same reply as a chat bubble — so the live and
    // restored views of one conversation would disagree.
    const next =
      typeof data?.turn === 'string' && data.turn === 'qa'
        ? applyQaFrame(current, payload)
        : applyStreamEvent(current, payload);

    // `seq` is additive on the wire and only present once stream persistence is on. It is
    // what a reattach passes as `?after_seq=`, so tracking the high-water mark here is
    // what makes gap recovery possible at all.
    const seq = typeof data?.seq === 'number' && Number.isFinite(data.seq) ? data.seq : null;

    set((s) => ({
      sessions: { ...s.sessions, [sessionId as SessionId]: next },
      streams: seq === null
        ? s.streams
        : {
            ...s.streams,
            [sessionId as SessionId]: {
              ...(s.streams[sessionId as SessionId] ?? blankStream()),
              lastSeq: Math.max(s.streams[sessionId as SessionId]?.lastSeq ?? 0, seq),
            },
          },
    }));

    return sessionId;
  },

  setUi: (sessionId, patch) => {
    if (!sessionId) return;
    set((state) => ({
      ui: { ...state.ui, [sessionId]: { ...(state.ui[sessionId] ?? blankUi()), ...patch } },
    }));
  },

  setVerification: (sessionId, patch) => {
    if (!sessionId) return;
    set((state) => {
      const existing = state.ui[sessionId] ?? blankUi();
      return {
        ui: {
          ...state.ui,
          [sessionId]: { ...existing, verification: { ...existing.verification, ...patch } },
        },
      };
    });
  },

  /**
   * Forget a session's client-side state (after an archive or delete).
   *
   * Its thread bindings go too, otherwise a late frame from a run that is still finishing
   * server-side would resurrect the entry — and it would come back as a bare
   * `blankSession()` with none of its history, which reads as a corrupted session rather
   * than an absent one.
   */
  dropSession: (sessionId) => {
    set((state) => {
      const sessions = { ...state.sessions };
      const streams = { ...state.streams };
      const ui = { ...state.ui };
      delete sessions[sessionId];
      delete streams[sessionId];
      delete ui[sessionId];
      const threadToSession = Object.fromEntries(
        Object.entries(state.threadToSession).filter(([, sid]) => sid !== sessionId),
      );
      return {
        sessions,
        streams,
        ui,
        threadToSession,
        activeSessionId: state.activeSessionId === sessionId ? null : state.activeSessionId,
      };
    });
  },

  /** Test-only, and used on logout: a new user must not inherit the previous one's tabs. */
  reset: () =>
    set({
      sessions: {},
      streams: {},
      ui: {},
      threadToSession: {},
      activeSessionId: null,
      unroutableFrames: 0,
    }),
}));
