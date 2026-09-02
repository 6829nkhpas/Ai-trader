"""Stream_Persister — write the transcript where it is produced, batched.

Why the writer is the streamer
------------------------------
Two requirements can only be met by persisting inside the SSE generator:

* *an incomplete streamed response must never appear as a complete one*, and
* *a client disconnect must not destroy server-side execution*.

Any design that puts a network hop between the generator and the store reopens the loss
window it was meant to close — the client's connection is exactly the thing that cannot
be relied on. So this writes from ``_run_events`` itself.

Why batched, and why the terminal frame is special
--------------------------------------------------
A REASONING stream is many small frames. One transaction per frame is the only thing that
would make SQLite the wrong choice here, so frames accumulate and flush on **size** (25),
on **age** (250 ms), and **unconditionally before any terminal frame**.

That last one is not an optimisation. If a terminal state could be written while frames
were still buffered, a reader could see a run marked ``complete`` whose transcript stopped
three frames earlier — a run that claims to have finished something it has no record of
doing. Flushing first makes "terminal implies the transcript is whole" true by
construction, and there is a property test asserting it.

Failure posture
---------------
Every store call is wrapped, and a failure DEGRADES: the frame is dropped from the record,
a WARN is logged once per run, and the live stream continues byte-for-byte unchanged. That
asymmetry is deliberate — an unwritable transcript costs history, whereas a persistence
error propagating into the generator would abort a live analysis the user is watching and
paying for. There is a test asserting the emitted SSE bytes are identical with persistence
enabled, disabled, and hard-failing.

What it does NOT do
-------------------
It does not decide anything. It records what the generator emitted. The terminal status it
writes is the one the generator already chose, and it never invents one — a run whose
process dies mid-stream is left ``streaming`` for
``session_store.reconcile_stale_runs`` to resolve at the next startup, precisely because
this object is not running any more and must not leave a guess behind.
"""

from __future__ import annotations

import os
import time
from typing import Any, List, Optional, Tuple

# Frames per transaction. 25 keeps a busy REASONING stream to a few writes a second while
# staying small enough that a crash loses little.
BATCH_SIZE = 25

# Maximum time a frame waits before being written, in seconds. Bounds the loss window on a
# quiet stream (a watching run emits a heartbeat every few minutes) so an unexpected death
# cannot strand a frame indefinitely.
BATCH_MAX_AGE_S = 0.25

ENV_ENABLED = "DEEP_QUANT_PERSIST_STREAM"

# Events whose ``content`` is the assistant's prose. Everything else — tool calls,
# verification steps, the structured decision — stays in ``run_events`` with its shape
# intact, because collapsing it into a text blob is exactly what the glass box must not do.
_CONTENT_EVENTS = ("REASONING", "TEXT_MESSAGE")

# Terminal event names. Used only to decide when to force a flush.
_TERMINAL_EVENTS = ("RUN_FINISHED", "ERROR")


def enabled() -> bool:
    """Whether stream persistence is active.

    Read per call so the flag is a container restart rather than a rebuild, matching every
    other switch in this deployment.
    """
    return (os.getenv(ENV_ENABLED) or "0").strip().lower() in ("1", "true", "yes", "on")


class StreamPersister:
    """Records one run's frames and the assistant message they compose.

    Constructed per run. A ``None`` ``run_id`` makes every method a no-op, which is what
    the legacy path (a ``/run`` carrying only a ``thread_id``, so no run row exists) and a
    disabled flag both produce — so callers never branch on whether persistence is on.
    """

    def __init__(
        self,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        *,
        kind: str = "run",
        store: Any = None,
    ):
        self.run_id = run_id
        self.session_id = session_id
        self.kind = kind
        self.message_id: Optional[str] = None
        self._buffer: List[Tuple[str, Any]] = []
        self._pending_text: List[str] = []
        self._opened_at = time.monotonic()
        self._flushed_at = time.monotonic()
        self._finalized = False
        self._warned = False
        # Injectable for tests; resolved lazily so importing this module costs nothing.
        self._store = store

    # ── Plumbing ─────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return bool(self.run_id) and enabled()

    def _resolve_store(self):
        if self._store is None:
            import session_store

            self._store = session_store
        return self._store

    def _warn(self, what: str, exc: Exception) -> None:
        """Log a persistence failure once per run.

        Once, not per frame: a broken store would otherwise emit a line per REASONING
        chunk and bury everything else in the log. The run id is included so the gap in
        the record can be found afterwards.
        """
        if self._warned:
            return
        self._warned = True
        print(
            f"[stream_persist] WARN: {what} failed for run={self.run_id} ({exc}). "
            f"The live stream is unaffected; this run's transcript will be incomplete."
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def open(self) -> Optional[str]:
        """Create the assistant message row as ``streaming``. Returns its id.

        Created up front rather than on first content so that a run which produces NOTHING
        — an immediate ERROR, an entitlement refusal, a dead LLM — still leaves a visible
        turn in the conversation. An empty answer the user can see beats a silent gap they
        cannot ask about.
        """
        if not self.active or self.message_id is not None:
            return self.message_id
        try:
            store = self._resolve_store()
            kind = (
                store.KIND_QA_ANSWER if self.kind == "qa" else store.KIND_ANALYSIS_ANSWER
            )
            message = store.create_message(
                session_id=self.session_id,
                role=store.ROLE_ASSISTANT,
                kind=kind,
                status=store.MSG_STREAMING,
                run_id=self.run_id,
                content="",
            )
            self.message_id = message["message_id"] if message else None
        except Exception as exc:  # noqa: BLE001 - never break the stream
            self._warn("creating the assistant message", exc)
        return self.message_id

    def add(self, event: str, payload: Any) -> None:
        """Buffer one frame, flushing if the batch is full, stale, or terminal."""
        if not self.active:
            return
        # A Q&A turn is a CHAT turn, so its frames are deliberately NOT appended to the run's
        # glass box. Only its prose is recorded, into the `qa_answer` message below.
        #
        # This is load-bearing, not tidiness. `/qa` REUSES the analysis run's `run_id`
        # (`_resolve_qa_thread`), so appending here put a second `RUN_STARTED` into that run's
        # event log. A reopened session replays that log through the live reducer
        # (`lib/fq/rehydrate.ts::replayEvents` -> `applyStreamEvent`), and the `RUN_STARTED`
        # branch RESETS the session — so a session with a follow-up question rebuilt the FIND
        # transcript and then threw it away, leaving the Q&A answer rendered as the entire
        # glass box. The live path never showed it: its Q&A handler ignores `RUN_STARTED`
        # outright (see `useQuantStore`), which is the same statement made on the write side.
        if self.kind != "qa":
            self._buffer.append((event, payload))

        if isinstance(payload, dict) and event in _CONTENT_EVENTS:
            content = payload.get("content")
            if isinstance(content, str) and content:
                self._pending_text.append(content)

        if (
            event in _TERMINAL_EVENTS
            or len(self._buffer) >= BATCH_SIZE
            or (time.monotonic() - self._flushed_at) >= BATCH_MAX_AGE_S
        ):
            self.flush()

    def flush(self) -> None:
        """Write the buffered frames and accumulated text. Never raises."""
        if not self.active or (not self._buffer and not self._pending_text):
            return
        frames, self._buffer = self._buffer, []
        text, self._pending_text = "".join(self._pending_text), []
        self._flushed_at = time.monotonic()
        try:
            store = self._resolve_store()
            if frames:
                store.append_run_events(self.run_id, frames)
            if text and self.message_id:
                store.append_message_delta(self.message_id, text)
        except Exception as exc:  # noqa: BLE001
            self._warn("writing a frame batch", exc)

    def finalize(self, terminal_status: str, *, detail: Optional[str] = None) -> None:
        """Record the run's and message's terminal state, exactly once.

        Flushes FIRST, so the transcript is whole before anything claims to have finished
        — see the module docstring. Set-once at both layers (``finalize_run`` and
        ``finalize_message`` both guard on their current state), so a duplicate
        ``RUN_FINISHED`` from a reattach cannot rewrite an outcome.

        ``terminal_status`` is the generator's own verdict, mapped rather than derived:
        this object never decides how a run ended.
        """
        if not self.active or self._finalized:
            return
        self._finalized = True
        self.flush()
        try:
            store = self._resolve_store()
            run_status, message_status = _map_terminal(store, terminal_status)
            store.finalize_run(self.run_id, run_status)
            if self.message_id:
                store.finalize_message(
                    self.message_id, message_status, error_detail=detail
                )
        except Exception as exc:  # noqa: BLE001
            self._warn(f"finalizing as {terminal_status}", exc)

    def mark_watching(self) -> None:
        """Record that the run has parked at a price watch.

        A paused run is NOT terminal — the watcher will wake it — so this is a status
        move rather than a finalize, and the assistant message deliberately stays
        ``streaming`` because more of the answer is genuinely still coming.
        """
        if not self.active:
            return
        self.flush()
        try:
            store = self._resolve_store()
            store.update_run_status(self.run_id, store.RUN_WATCHING)
        except Exception as exc:  # noqa: BLE001
            self._warn("marking the run as watching", exc)

    def record_disconnect(self) -> None:
        """Called from the generator's ``finally``. Idempotent.

        A client that hangs up mid-analysis leaves a real, partial answer, and this is
        where it is recorded as ``truncated`` with the text it did produce. Idempotent
        because a run that reached a terminal event has already finalized itself, and this
        must not overwrite ``complete`` with ``truncated``.

        The RUN keeps executing — this only records what the client received. That is the
        "a disconnect must not destroy server-side execution" half of the requirement.
        """
        if not self.active or self._finalized:
            return
        self._finalized = True
        self.flush()
        try:
            store = self._resolve_store()
            store.finalize_run(self.run_id, store.RUN_TRUNCATED)
            if self.message_id:
                store.finalize_message(self.message_id, store.MSG_TRUNCATED)
        except Exception as exc:  # noqa: BLE001
            self._warn("recording a disconnect", exc)


def _map_terminal(store: Any, terminal_status: str) -> Tuple[str, str]:
    """Map the generator's terminal word onto (run status, message status).

    The two vocabularies are separate on purpose — a run is a unit of execution and a
    message is a unit of conversation — but they have to agree about what happened, so the
    mapping lives in one place rather than at each call site.

    ``paused`` never reaches here: it is not terminal, and it is handled by
    ``mark_watching``. Anything unrecognised becomes ``truncated``, which is the honest
    default: a status this code does not understand is not evidence of completion.
    """
    table = {
        "completed": (store.RUN_COMPLETE, store.MSG_COMPLETE),
        "complete": (store.RUN_COMPLETE, store.MSG_COMPLETE),
        "cancelled": (store.RUN_CANCELLED, store.MSG_CANCELLED),
        "error": (store.RUN_ERROR, store.MSG_ERROR),
        "truncated": (store.RUN_TRUNCATED, store.MSG_TRUNCATED),
        "disconnected": (store.RUN_TRUNCATED, store.MSG_TRUNCATED),
    }
    return table.get((terminal_status or "").strip().lower(), (store.RUN_TRUNCATED, store.MSG_TRUNCATED))
