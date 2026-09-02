"""Session_API — the authenticated HTTP surface over ``session_store``.

Mounted on the deep-quant app only when ``DEEP_QUANT_SESSIONS_ENABLED`` is on, so this
whole file is inert until the rollout reaches it.

Separate module, not more of ``main.py``
---------------------------------------
``main.py`` is already the SSE lifecycle, the entitlement gate, the interaction log, the
fan-out hub and the metrics surface. These eight CRUD routes have nothing to do with any
of that, and an ``APIRouter`` keeps them reviewable on their own.

Two conventions that are load-bearing
-------------------------------------
**404, never 403, for a session the caller does not own.** A 403 confirms the id exists,
which turns any endpoint into an enumeration oracle. ``session_store`` supports this by
having no ownership-free read at all: ``get_session_for_user`` returns ``None`` for both
"missing" and "someone else's", so the two are indistinguishable here by construction
rather than by a branch someone could reorder.

**409 for an immutable field.** ``symbol`` and ``profile`` define a session's identity,
so changing them is not a malformed request (422) — it is a well-formed request for an
operation that does not exist on this entity.

Response shapes
---------------
``SessionSummary`` carries exactly what the tab bar and the history list need. The tab
must render ``RELIANCE - 10m - 10:31`` from the session row alone, without the client
reconstructing it from its own state, which is what makes a reopened session render
correctly before its messages have loaded.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import session_store as store
from internal_identity import resolve_user

router = APIRouter()


def sessions_enabled() -> bool:
    """Whether the session surface is mounted.

    Read at import by ``main.py`` (a router cannot be mounted conditionally per
    request), so flipping this is a container restart — the same cost as every other
    switch in this deployment.
    """
    return (os.getenv("DEEP_QUANT_SESSIONS_ENABLED") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# ── Validation ────────────────────────────────────────────────────────────────

# The four terminal workspaces (`useTradeStore.activeProfile`). A closed allowlist here
# and NOT for symbol/timeframe, for a specific reason: profile is IMMUTABLE on a session
# and it changes what the agent actually does (which data domain it leads with, and the
# R:R floor `declare_trade` enforces — 1:1.3 for INTRADAY, 1:2 elsewhere). A typo is
# therefore permanent and behavioural, so it is worth refusing at the boundary.
VALID_PROFILES = ("INTRADAY", "SWING", "INVESTOR", "FNO")

# Symbol and timeframe get SHAPE validation only, deliberately.
#
# The authoritative timeframe vocabulary lives in the charting layer
# (`RESOLUTION_TO_TIMEFRAME` in `frontend/src/charting/datafeed.ts`) and the symbol
# universe is every NSE ticker plus every F&O tradingsymbol. A second copy of either
# here would drift from the real one, and the failure mode of a stale allowlist is
# refusing a legitimate instrument — worse than accepting an odd string that the agent
# already degrades on honestly. `/run` has never validated these either, so this also
# avoids the session surface being stricter than the run it creates.
MAX_SYMBOL_CHARS = 64
MAX_TIMEFRAME_CHARS = 16
MAX_TITLE_CHARS = 200


def _clean(value: str, field: str, cap: int) -> str:
    """Trim, reject empty/oversize, and reject control characters.

    Control characters are refused rather than stripped because these are identifiers
    that end up in a UI label and a log line. NUL specifically is the one that matters:
    ``'\\x00'.strip()`` is truthy in Python but SQLite's ``length('\\x00')`` is 0, so
    without this the store's CHECK constraint fires and a 422 arrives as a 500. Found by
    a property test against ``session_store``; refused here so the API answers cleanly
    instead of relying on the store to catch it.
    """
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    if len(text) > cap:
        raise HTTPException(status_code=422, detail=f"{field} must be at most {cap} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise HTTPException(status_code=422, detail=f"{field} contains control characters")
    return text


def _clean_profile(value: str) -> str:
    text = _clean(value, "profile", 32).upper()
    if text not in VALID_PROFILES:
        raise HTTPException(
            status_code=422,
            detail=f"profile must be one of {', '.join(VALID_PROFILES)}",
        )
    return text


# ── Request / response models ─────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    symbol: str
    profile: str = "INTRADAY"
    timeframe: str
    title: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class PatchSessionRequest(BaseModel):
    """Every field optional; only those PRESENT are applied.

    ``model_fields_set`` distinguishes "not supplied" from "supplied as null", which
    matters for ``title``: sending ``null`` is how a client clears a rename back to the
    derived label, and treating that as "unchanged" would make the clear silently fail.
    """

    title: Optional[str] = None
    timeframe: Optional[str] = None
    status: Optional[str] = None
    # Present so a client can retarget Q&A grounding at an earlier run in the session.
    active_run_id: Optional[str] = None
    # Accepted so a 409 can be returned with a useful message rather than a bare
    # "unknown field". Silently ignoring them would let a client believe a rename worked.
    symbol: Optional[str] = None
    profile: Optional[str] = None


class SessionSummary(BaseModel):
    """What the tab bar and the history list need, and nothing more."""

    session_id: str
    title: Optional[str]
    symbol: str
    timeframe: str
    profile: str
    status: str
    created_at: float
    updated_at: float
    archived_at: Optional[float] = None
    active_run_id: Optional[str] = None
    message_count: int = 0
    last_run: Optional[Dict[str, Any]] = None


class SessionListResponse(BaseModel):
    items: List[SessionSummary]
    next_cursor: Optional[str] = None


class MessageListResponse(BaseModel):
    items: List[Dict[str, Any]]
    last_seq: int


class RunListResponse(BaseModel):
    items: List[Dict[str, Any]]


class RunEventsResponse(BaseModel):
    items: List[Dict[str, Any]]
    last_seq: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _caller(request: Request) -> str:
    """The verified user id, or 401.

    Unlike ``/run``, there is NO body-``user_id`` fallback here even when
    ``DEEP_QUANT_REQUIRE_IDENTITY`` is off. That asymmetry is the point: ``/run`` has to
    keep working for existing clients through the rollout, whereas these routes return
    stored per-user data and are new, so there is no compatibility to preserve and no
    reason to ever serve them to an unidentified caller.

    In an unenforced deployment ``resolve_user`` still accepts a valid assertion when one
    is present, so this surface is reachable as soon as the Next tier is minting them.
    """
    user_id = resolve_user(request, None, surface="/sessions")
    if not user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    return user_id


def _not_found() -> HTTPException:
    """404 for missing AND for not-owned.

    One helper so the two cases cannot drift into different responses — a 403 (or a
    different message) on the not-owned path would confirm the id exists.
    """
    return HTTPException(status_code=404, detail="session not found")


def _summarise(session: Dict[str, Any], user_id: str) -> SessionSummary:
    """Decorate a stored session with the counts the list UI needs.

    ``message_count`` and ``last_run`` are computed on read rather than denormalised onto
    the session row. They change on every streamed flush, and a counter maintained by the
    write path is a counter that eventually disagrees with the rows it counts.
    """
    runs = store.list_runs(session["session_id"], user_id)
    messages, last_seq = store.list_messages(session["session_id"], user_id, limit=1)
    last_run = None
    if runs:
        newest = runs[-1]
        last_run = {
            "run_id": newest["run_id"],
            "kind": newest["kind"],
            "status": newest["status"],
            "started_at": newest["started_at"],
            "ended_at": newest["ended_at"],
        }
    return SessionSummary(
        session_id=session["session_id"],
        title=session["title"],
        symbol=session["symbol"],
        timeframe=session["timeframe"],
        profile=session["profile"],
        status=session["status"],
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        archived_at=session["archived_at"],
        active_run_id=session["active_run_id"],
        message_count=last_seq,
        last_run=last_run,
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/sessions", status_code=201)
async def create_session(payload: CreateSessionRequest, request: Request) -> SessionSummary:
    """Create a session owned by the verified caller.

    The caller cannot choose the owner. ``user_id`` is not in the request model at all —
    which is the whole point of this migration, since the previous design took it from
    the request body and verified nothing.
    """
    user_id = _caller(request)
    session = store.create_session(
        user_id=user_id,
        symbol=_clean(payload.symbol, "symbol", MAX_SYMBOL_CHARS),
        profile=_clean_profile(payload.profile),
        timeframe=_clean(payload.timeframe, "timeframe", MAX_TIMEFRAME_CHARS),
        title=_clean(payload.title, "title", MAX_TITLE_CHARS) if payload.title else None,
        metadata=payload.metadata,
    )
    return _summarise(session, user_id)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    status: Optional[str] = Query(default=store.SESSION_ACTIVE),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    q: Optional[str] = Query(default=None),
) -> SessionListResponse:
    """One page of the caller's sessions, newest activity first.

    ``status`` is restricted to ``active``/``archived``. ``deleted`` is not listable and
    ``None`` (every status) is a diagnostic capability in the store that this surface
    deliberately does not expose.
    """
    user_id = _caller(request)
    if status not in (store.SESSION_ACTIVE, store.SESSION_ARCHIVED):
        raise HTTPException(
            status_code=422,
            detail=f"status must be {store.SESSION_ACTIVE} or {store.SESSION_ARCHIVED}",
        )
    items, next_cursor = store.list_sessions(
        user_id, status=status, cursor=cursor, limit=limit, query=q
    )
    return SessionListResponse(
        items=[_summarise(s, user_id) for s in items], next_cursor=next_cursor
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> SessionSummary:
    user_id = _caller(request)
    session = store.get_session_for_user(session_id, user_id)
    if session is None or session["status"] == store.SESSION_DELETED:
        raise _not_found()
    return _summarise(session, user_id)


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str, payload: PatchSessionRequest, request: Request
) -> SessionSummary:
    """Rename, retime, archive, reopen, or retarget Q&A grounding.

    ``symbol``/``profile`` return 409. They are answered explicitly rather than falling
    through to "unknown field" so the client can show why, and so a client attempting one
    is never left believing it succeeded.
    """
    user_id = _caller(request)

    # Read BEFORE patching, so a deleted session cannot be resurrected.
    #
    # Checking the POST-update row is not equivalent and was the bug: `status: active`
    # writes first, so the row is no longer `deleted` by the time it is inspected and the
    # patch succeeds. A user who deleted a conversation would find it back, and an
    # automatic client retry could do it without anyone asking. Caught by
    # `test_a_deleted_session_cannot_be_reopened_by_patch`.
    existing = store.get_session_for_user(session_id, user_id)
    if existing is None or existing["status"] == store.SESSION_DELETED:
        raise _not_found()

    supplied = payload.model_fields_set
    for immutable in ("symbol", "profile"):
        if immutable in supplied:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{immutable} is immutable on a session. It defines the session's "
                    f"identity; create a new session instead."
                ),
            )

    patch: Dict[str, Any] = {}
    if "title" in supplied:
        # `null` clears the rename back to the derived label; a blank string means the
        # same thing. Distinguishing "not supplied" from "supplied as null" is what
        # `model_fields_set` is for.
        patch["title"] = (
            _clean(payload.title, "title", MAX_TITLE_CHARS) if payload.title else None
        )
    if "timeframe" in supplied and payload.timeframe is not None:
        patch["timeframe"] = _clean(payload.timeframe, "timeframe", MAX_TIMEFRAME_CHARS)
    if "active_run_id" in supplied:
        patch["active_run_id"] = _validated_run_target(session_id, user_id, payload.active_run_id)
    if "status" in supplied and payload.status is not None:
        if payload.status not in (store.SESSION_ACTIVE, store.SESSION_ARCHIVED):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"status must be {store.SESSION_ACTIVE} or {store.SESSION_ARCHIVED}; "
                    f"use DELETE to remove a session"
                ),
            )
        patch["status"] = payload.status

    try:
        updated = store.update_session(session_id, user_id, patch=patch)
    except store.ImmutableFieldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if updated is None or updated["status"] == store.SESSION_DELETED:
        raise _not_found()
    return _summarise(updated, user_id)


def _validated_run_target(session_id: str, user_id: str, run_id: Optional[str]) -> Optional[str]:
    """Check that ``run_id`` is a run of THIS session before it becomes the Q&A target.

    Without this, a caller could point ``active_run_id`` at a run in another of their
    sessions — or at a run that does not exist — and every subsequent Q&A would either
    ground in the wrong analysis or fail obscurely. Cross-user is already impossible
    (``get_run_for_user`` is owner-scoped); this closes the cross-SESSION case, which is
    a correctness bug rather than a security one.
    """
    if run_id is None:
        return None
    run = store.get_run_for_user(run_id, user_id)
    if run is None or run["session_id"] != session_id:
        raise HTTPException(status_code=422, detail="active_run_id is not a run of this session")
    return run_id


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    hard: bool = Query(default=False),
) -> Dict[str, Any]:
    """Delete a session. Soft by default.

    ``hard=true`` scrubs messages and transcripts and keeps a tombstone. It touches only
    the session store: ``interaction_log`` and ``recommendations`` are the append-only
    five-year record and a user deleting a conversation does not erase the audit trail.
    """
    user_id = _caller(request)
    result = store.delete_session(session_id, user_id, hard=hard)
    if result is None:
        raise _not_found()
    return {"session_id": session_id, "status": result["status"], "hard": hard}


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> MessageListResponse:
    """One page of the conversation, in order.

    ``after_seq`` rather than an offset: ``seq`` is dense per session, so "everything
    after what I have" is exact even while the session is streaming.

    A non-owned session yields an empty page from the store; it is turned into a 404 here
    so a caller cannot distinguish "empty conversation" from "not yours" — the same
    reasoning as the session read.
    """
    user_id = _caller(request)
    session = store.get_session_for_user(session_id, user_id)
    if session is None or session["status"] == store.SESSION_DELETED:
        raise _not_found()
    items, last_seq = store.list_messages(
        session_id, user_id, after_seq=after_seq, limit=limit
    )
    return MessageListResponse(items=items, last_seq=last_seq)


@router.get("/sessions/{session_id}/runs")
async def list_runs(session_id: str, request: Request) -> RunListResponse:
    user_id = _caller(request)
    session = store.get_session_for_user(session_id, user_id)
    if session is None or session["status"] == store.SESSION_DELETED:
        raise _not_found()
    return RunListResponse(items=store.list_runs(session_id, user_id))


@router.get("/runs/{run_id}/events")
async def list_run_events(
    run_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> RunEventsResponse:
    """The durable glass-box transcript for a run.

    This is the read side of rehydration: the client feeds these frames through the same
    reducer a live stream drives, so a reopened session renders identically to one that
    was watched live and there is no second rendering path to drift.

    Ownership is checked on the RUN, not the session, because ``runs.user_id`` is
    denormalised for exactly this — one indexed read, no join.
    """
    user_id = _caller(request)
    run = store.get_run_for_user(run_id, user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    items, last_seq = store.list_run_events(run_id, after_seq=after_seq, limit=limit)
    return RunEventsResponse(items=items, last_seq=last_seq)
