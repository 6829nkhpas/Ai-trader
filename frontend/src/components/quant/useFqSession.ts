// components/quant/useFqSession.ts
//
// THE ONE PLACE that knows whether Find Quant Trade is reading per-session state or the
// legacy flat store.
//
// Components could branch on `FQ_MULTI_SESSION` themselves, but then flipping the flag
// permanently would mean editing every one of them again — six files, each a chance to miss a
// branch. Routing every read through here means Phase 11 deletes one branch in one file and
// the components never change again.
//
// WHY PER-FIELD HOOKS instead of one hook returning an object: a hook returning
// `{ reasoningSteps, sessionStatus, ... }` builds a new object every render, so zustand's
// `Object.is` check never matches and the component re-renders on every frame of every
// session. The components already select individual fields; these hooks preserve that exactly,
// which is the difference between one streaming tab and eight being usable.
//
// Both stores are read UNCONDITIONALLY in each hook. `FQ_MULTI_SESSION` is a build-time
// constant so a conditional hook call would be safe in practice, but it would still violate
// the rules of hooks and trip lint. Reading both and picking costs one extra subscription to a
// store that nothing writes to on the inactive path. This is only safe because the session
// selectors return reference-stable empty values — see the note in `sessionSelectors.ts`.

import { FQ_MULTI_SESSION } from '../../lib/env';
import { bridgeInvoke } from '../../lib/bridge';
import { useAuthStore } from '../../store/useAuthStore';
import { MODEL_SELECTION_LOCKED, useQuantStore } from '../../store/useQuantStore';
import { useSessionStore } from '../../store/useSessionStore';
import {
  selectAiPlan,
  selectAnalysisError,
  selectCanAskQuestion,
  selectCurrentRunId,
  selectCurrentThreadId,
  selectFinalTrade,
  selectIsAnalyzing,
  selectQaMessages,
  selectQaStatus,
  selectReasoningSteps,
  selectSessionStatus,
  selectUiFor,
} from '../../store/sessionSelectors';
import type { SessionUiState } from '../../store/useSessionStore';
import type { AiExecutionPlan, QaChatMessage, ReasoningStep } from '../../store/useQuantStore';

/** Which session the workspace is showing. `null` on the legacy path, which has no ids. */
export function useFqActiveSessionId(): string | null {
  const id = useSessionStore((s) => s.activeSessionId);
  return FQ_MULTI_SESSION ? id : null;
}

// ── Run state ────────────────────────────────────────────────────────────────

export function useFqSessionStatus() {
  const next = useSessionStore(selectSessionStatus);
  const legacy = useQuantStore((s) => s.sessionStatus);
  return FQ_MULTI_SESSION ? next : legacy;
}

export function useFqIsAnalyzing(): boolean {
  const next = useSessionStore(selectIsAnalyzing);
  const legacy = useQuantStore((s) => s.isAnalyzing);
  return FQ_MULTI_SESSION ? next : legacy;
}

export function useFqReasoningSteps(): ReasoningStep[] {
  const next = useSessionStore(selectReasoningSteps);
  const legacy = useQuantStore((s) => s.reasoningSteps);
  return FQ_MULTI_SESSION ? next : legacy;
}

export function useFqFinalTrade(): AiExecutionPlan | null {
  const next = useSessionStore(selectFinalTrade);
  const legacy = useQuantStore((s) => s.finalTrade);
  return FQ_MULTI_SESSION ? next : legacy;
}

export function useFqAiPlan(): AiExecutionPlan | null {
  const next = useSessionStore(selectAiPlan);
  const legacy = useQuantStore((s) => s.aiPlan);
  return FQ_MULTI_SESSION ? next : legacy;
}

export function useFqAnalysisError(): string | null {
  const next = useSessionStore(selectAnalysisError);
  const legacy = useQuantStore((s) => s.analysisError);
  return FQ_MULTI_SESSION ? next : legacy;
}

/**
 * The thread grounding Q&A and cancel.
 *
 * On the new path this is the ACTIVE session's thread, read from that session's own stream
 * record. The legacy field was global, so switching tabs mid-question asked the backend about
 * whichever analysis happened to be on screen.
 */
export function useFqThreadId(): string | null {
  const next = useSessionStore(selectCurrentThreadId);
  const legacy = useQuantStore((s) => s.currentThreadId);
  return FQ_MULTI_SESSION ? next : legacy;
}

/** The server-minted run id, for cancel and for grounding a Q&A in a specific run. */
export function useFqRunId(): string | null {
  const next = useSessionStore(selectCurrentRunId);
  // The legacy store never had one — it addressed runs by thread.
  return FQ_MULTI_SESSION ? next : null;
}

// ── Q&A ──────────────────────────────────────────────────────────────────────

export function useFqQaMessages(): QaChatMessage[] {
  const next = useSessionStore(selectQaMessages);
  const legacy = useQuantStore((s) => s.qaMessages);
  return FQ_MULTI_SESSION ? next : legacy;
}

export function useFqQaStatus() {
  const next = useSessionStore(selectQaStatus);
  const legacy = useQuantStore((s) => s.qaStatus);
  return FQ_MULTI_SESSION ? next : legacy;
}

/**
 * Whether the composer may send.
 *
 * The legacy expression is reproduced rather than referenced because it lived inline in
 * `TradeQaPanel`: unlocked at `watching`/`complete`, requires a thread, and blocked while a
 * Q&A is already streaming. `selectCanAskQuestion` is the same rule evaluated PER SESSION —
 * as a flat field, one session's Q&A blocked every other session's.
 */
export function useFqCanAskQuestion(): boolean {
  const next = useSessionStore(selectCanAskQuestion);
  const legacyStatus = useQuantStore((s) => s.sessionStatus);
  const legacyThread = useQuantStore((s) => s.currentThreadId);
  const legacyQa = useQuantStore((s) => s.qaStatus);
  if (FQ_MULTI_SESSION) return next;
  const unlocked = legacyStatus === 'watching' || legacyStatus === 'complete';
  return unlocked && !!legacyThread && legacyQa !== 'streaming';
}

/**
 * Ask a follow-up, grounded in a NAMED session and run.
 *
 * The legacy implementation read its thread id from a flat "current" store field, so switching
 * tabs mid-question asked the backend about whichever analysis was on screen. Here the session
 * and the run are captured at call time and sent explicitly.
 *
 * Only the USER turn is inserted optimistically. There is deliberately no assistant placeholder:
 * its id has to be `qa-<run_id>` to match the live frames, and the run id does not exist until
 * the server replies. `applyQaFrame` creates the assistant turn from the first frame instead,
 * which also means a reattach renders the answer without any placeholder having existed.
 */
export function useFqAskQuestion(): (question: string) => void {
  const legacy = useQuantStore((s) => s.askQuestion);
  const selectedModel = useQuantStore((s) => s.selectedModel);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const runId = useSessionStore(selectCurrentRunId);
  const userId = useAuthStore((s) => s.user?.id ?? null);

  if (!FQ_MULTI_SESSION) return legacy;

  return (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || !activeSessionId) return;

    // Doubles as the optimistic turn's id and the server's idempotency key, so a retried press
    // cannot produce two copies of the same question — and the row the server persists carries
    // the same id the client already rendered.
    const clientMsgId = newClientMsgId();
    const store = useSessionStore.getState();
    const existing = store.sessions[activeSessionId]?.qaMessages ?? [];

    store.upsertSession(activeSessionId, {
      qaMessages: [...existing, { id: clientMsgId, role: 'user', content: trimmed }],
      // Locks the composer immediately. Waiting for the first frame leaves a window in which a
      // second press sends the same question again.
      qaStatus: 'streaming',
    });

    void bridgeInvoke<void>('ask_trade_question', {
      session_id: activeSessionId,
      context_run_id: runId,
      question: trimmed,
      model: MODEL_SELECTION_LOCKED ? null : selectedModel || null,
      client_msg_id: clientMsgId,
      userId,
    }).catch((err: unknown) => {
      // The stream never started, so no ERROR frame is coming and nothing else will unlock the
      // composer.
      const message = err instanceof Error ? err.message : String(err);
      const s = useSessionStore.getState();
      const current = s.sessions[activeSessionId]?.qaMessages ?? [];
      s.upsertSession(activeSessionId, {
        qaMessages: [
          ...current,
          { id: `${clientMsgId}-error`, role: 'assistant', content: message, error: true },
        ],
        qaStatus: 'idle',
      });
    });
  };
}

/** A client-side id for an optimistic turn, also used as the server idempotency key. */
function newClientMsgId(): string {
  // `randomUUID` needs a secure context and is absent in some embedded webviews, so the
  // fallback is not decorative — without it the composer would throw on send.
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `qa-msg-${uuid}`;
  return `qa-msg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// ── Per-session UI state ─────────────────────────────────────────────────────
//
// `mode`, the composer draft and the verification form were component-local `useState` or flat
// store fields, so switching sessions silently reset a half-typed question and a half-filled
// verification form. They are now per session; on the legacy path there is exactly one session,
// so the same store slot is used with a fixed key and behaviour is unchanged.

/** The session whose UI slice the setters below write to. */
const LEGACY_UI_KEY = '__legacy__';

function uiKey(activeSessionId: string | null): string {
  return activeSessionId ?? LEGACY_UI_KEY;
}

/**
 * The key this component reads AND writes.
 *
 * Read and write must resolve the same slot. Selecting via `activeSessionId` while writing to a
 * fixed legacy key would mean the legacy path never observes its own writes — a mode toggle
 * that does nothing.
 */
function useUiKey(): string {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  return uiKey(FQ_MULTI_SESSION ? activeSessionId : null);
}

export function useFqMode(): [SessionUiState['mode'], (mode: SessionUiState['mode']) => void] {
  const key = useUiKey();
  // A primitive, so the component re-renders only when the mode itself changes — not when the
  // draft in the same slice changes.
  const mode = useSessionStore((s) => selectUiFor(key)(s).mode);
  const setUi = useSessionStore((s) => s.setUi);
  return [mode, (next) => setUi(key, { mode: next })];
}

export function useFqDraft(): [string, (draft: string) => void] {
  const key = useUiKey();
  const draft = useSessionStore((s) => selectUiFor(key)(s).draft);
  const setUi = useSessionStore((s) => s.setUi);
  return [draft, (next) => setUi(key, { draft: next })];
}

export function useFqVerification() {
  const key = useUiKey();
  const verification = useSessionStore((s) => selectUiFor(key)(s).verification);
  const setVerification = useSessionStore((s) => s.setVerification);
  return {
    verification,
    setVerification: (patch: Parameters<typeof setVerification>[1]) => setVerification(key, patch),
  };
}
