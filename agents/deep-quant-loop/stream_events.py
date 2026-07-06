"""Pure helpers for the glass-box SSE stream (``main.py`` ``event_generator``).

Factored out of ``main.py`` so the reasoning splitter and the per-event payload
builders are unit- and property-testable in isolation (tasks 15.3-15.9, design
Properties 53-59).

This module implements the **glass-box event vocabulary** (Requirement 16):

  * ``REASONING``         natural-language monologue with all raw tool-call
                          markup stripped (R16.1, R16.8)
  * ``TOOL_CALL_START``   tool name + supplied arguments (R16.2)
  * ``TOOL_CALL_RESULT``  tool name + returned result or a structured summary
                          (R16.3)
  * ``TOOL_CALL_END``     tool name + terminal status, with ``error_reason`` on
                          failure (R16.4, R16.5)
  * ``VERIFICATION_STEP`` a self-verification / risk-manager check with its
                          outcome (R16.6)
  * ``DECISION``          action + conviction score + rationale (R16.7)

The DeepSeek/HuggingFace custom-token regexes are **reused** from ``graph.py``
so the markup vocabulary recognised by the reasoning splitter stays identical to
the one recognised by the tool-call extractor (a single source of truth for the
token grammar). Everything in this module is a pure function of its arguments —
no I/O, no clock, no ambient state — so each helper can be exercised directly by
the property tests without invoking the LLM or the graph.

Run-lifecycle, ordering guarantees, and the ERROR path (task 15.2) are also
implemented here as pure helpers — the lifecycle event builders, the per-update
event expansion (:func:`node_update_events`), and the ordered run assembler
(:func:`assemble_run_events`) — so the ordering/resilience property tests
(tasks 15.10-15.14, Properties 60-64) can target them without spinning up the
ASGI app or the live LLM. ``main.py`` mirrors the same ordering around the live
async stream.
"""

import json
import re
from typing import Any, Iterator, List, Optional, Tuple

# Reuse graph.py's custom-token vocabulary and parsing primitives so the
# reasoning splitter strips exactly the markup the extractor recognises.
from graph import (  # noqa: E402
    _CALL_BLOCK_RE,
    _SEP_NAME_RE,
    _extract_balanced_json,
    _tool_result_is_error,
    _parse_tool_content,
)

# ── Markup token literals (orphan-token cleanup) ─────────────────────────────
# Stray begin/sep/end tokens that survive structural stripping (e.g. an
# unterminated call block) are scrubbed so no raw markup token can leak into a
# REASONING event (R16.8 / Property 59).
_BEGIN_TOKEN = "<｜tool▁call▁begin｜>"
_SEP_TOKEN = "<｜tool▁sep｜>"
_END_TOKEN = "<｜tool▁call▁end｜>"
_ORPHAN_TOKEN_RE = re.compile(
    "|".join(re.escape(t) for t in (_BEGIN_TOKEN, _SEP_TOKEN, _END_TOKEN))
)

# Largest serialized tool result emitted verbatim before it is summarized.
_RESULT_SUMMARY_MAX_CHARS = 4000


# ── Stream-event names ───────────────────────────────────────────────────────
# Centralised so ``main.py`` and the property tests refer to the same literals.

RUN_STARTED = "RUN_STARTED"
RUN_FINISHED = "RUN_FINISHED"
ERROR = "ERROR"
REASONING = "REASONING"
TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
TOOL_CALL_END = "TOOL_CALL_END"
VERIFICATION_STEP = "VERIFICATION_STEP"
DECISION = "DECISION"
# The interim, non-committal Best_Current_Read surfaced on a stand_aside / HOLD
# (and, when heartbeat is enabled, during the wait) — adaptive-opportunity-engine
# R8.1/R8.2/R8.4. It is an assessment, NEVER a committed trade.
BEST_CURRENT_READ = "BEST_CURRENT_READ"

# Terminal outcomes of a run (the ``RUN_FINISHED`` status set, R17.2/R17.6).
RUN_COMPLETED = "completed"
RUN_PAUSED = "paused"
# Internal-only sentinel: the LLM stream failed (drives the ERROR path, R17.5).
RUN_ERROR = "error"


# ── JSON-object payload normalization (R17.7) ────────────────────────────────

def ensure_json_object(data: Any) -> dict:
    """Coerce a stream payload into a JSON-serializable **object** (R17.7).

    Every Stream_Event's ``data`` payload must be a valid JSON object. This pure
    helper is the single choke-point that guarantees it:

      * a ``dict`` is returned as-is (it serializes as a JSON object);
      * any non-object payload is wrapped as ``{"value": <payload>}`` so the
        emitted ``data`` is still a JSON object rather than a bare scalar/array.

    It does not attempt to verify deep serializability — ``format_sse`` serializes
    with a ``default=str`` fallback so no payload can ever break the framing.
    Factored out (with :func:`format_sse`) so the ordering/JSON-object property
    tests (tasks 15.10-15.14, Property 64) can target it directly.
    """
    if isinstance(data, dict):
        return data
    return {"value": data}


# ── SSE framing ──────────────────────────────────────────────────────────────

def format_sse(event_name: str, data: Any) -> str:
    """Frame ``data`` as a Server-Sent Event with name ``event_name``.

    The payload is normalized to a JSON object via :func:`ensure_json_object`
    and serialized with a ``default=str`` fallback, so the ``data`` line is
    **always** a valid JSON object string and serialization can never raise
    (R17.7). Kept here (rather than inline in ``main.py``) so tests can assert
    the framing and the JSON-validity of every event payload in one place.
    """
    payload = ensure_json_object(data)
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


# ── Reasoning splitter (markup stripping) ────────────────────────────────────

def _strip_standalone_sep_calls(text: str) -> str:
    """Remove standalone ``<｜tool▁sep｜>name{json}`` segments from ``text``.

    Tier-2 markup (separator token + tool name + brace-balanced JSON args
    without an enclosing call block) is excised so its tool name and JSON args
    never appear inside reasoning. The scan is left-to-right and always removes
    at least the separator token + name, so it terminates.
    """
    while True:
        m = _SEP_NAME_RE.search(text)
        if not m:
            return text
        raw_args = _extract_balanced_json(text, m.end())
        if raw_args is not None:
            args_pos = text.find(raw_args, m.end())
            end_idx = args_pos + len(raw_args) if args_pos != -1 else m.end()
        else:
            end_idx = m.end()
        text = text[: m.start()] + text[end_idx:]


def strip_tool_call_markup(content: Any) -> str:
    """Return ``content`` with all raw tool-call markup removed (R16.8).

    Removes, in order: (1) complete DeepSeek call blocks delimited by the
    begin/end tokens — including the inner separator, tool name, and JSON args;
    (2) standalone separator-token call segments and their JSON args; and (3) any
    orphaned begin/sep/end tokens left behind. What remains is the
    natural-language reasoning, with surrounding whitespace collapsed at the
    edges. Guarantees that no raw tool-call markup token survives (Property 59).
    """
    if not content:
        return ""
    text = content if isinstance(content, str) else str(content)
    # 1. Whole call blocks (begin ... end) including their inner name + args.
    text = _CALL_BLOCK_RE.sub("", text)
    # 2. Standalone separator-token call segments and their JSON args.
    text = _strip_standalone_sep_calls(text)
    # 3. Any orphaned markup tokens.
    text = _ORPHAN_TOKEN_RE.sub("", text)
    return text.strip()


def build_reasoning_event(content: Any, role: Optional[str] = None) -> Optional[dict]:
    """Build a ``REASONING`` payload from an assistant message's content.

    Strips all tool-call markup (R16.8) and returns ``{"content": <text>}`` only
    when natural-language reasoning remains; returns ``None`` when the content
    was empty or consisted solely of markup, so the caller emits no event for it
    (R16.1).

    When the source message carries a ``role`` tag (one of ``bull`` / ``bear`` /
    ``judge`` set by the debate role nodes, multi-agent-debate R8.1), it is
    surfaced on the payload so the distinct role reasoning events are
    distinguishable. Untagged (single-agent / non-debate) reasoning omits the
    ``role`` key, leaving the existing payload shape unchanged.
    """
    stripped = strip_tool_call_markup(content)
    if not stripped:
        return None
    payload = {"content": stripped}
    if isinstance(role, str) and role.strip():
        payload["role"] = role.strip()
    return payload


# ── Tool-call event builders ─────────────────────────────────────────────────

def build_tool_call_start_event(tool_name: str, args: Any) -> dict:
    """Build a ``TOOL_CALL_START`` payload: tool name + supplied args (R16.2)."""
    return {"tool": tool_name, "args": args if args is not None else {}}


def _summarize_result(parsed: Any) -> dict:
    """Produce a compact structured summary of an oversized parsed result."""
    if isinstance(parsed, dict):
        return {
            "summary_keys": sorted(str(k) for k in parsed.keys()),
            "field_count": len(parsed),
            "truncated": True,
        }
    if isinstance(parsed, list):
        return {"summary_type": "list", "length": len(parsed), "truncated": True}
    return {"summary": str(parsed)[:_RESULT_SUMMARY_MAX_CHARS], "truncated": True}


def _result_payload(content: Any) -> Tuple[Any, bool]:
    """Return ``(result_or_summary, summarized)`` for a tool result payload.

    Small results are returned verbatim (parsed when possible); results whose
    serialized form exceeds ``_RESULT_SUMMARY_MAX_CHARS`` are replaced by a
    structured summary so the stream stays readable (R16.3).
    """
    parsed = _parse_tool_content(content)
    if parsed is None:
        text = "" if content is None else (content if isinstance(content, str) else str(content))
        if len(text) > _RESULT_SUMMARY_MAX_CHARS:
            return {"summary": text[:_RESULT_SUMMARY_MAX_CHARS], "truncated": True, "original_length": len(text)}, True
        return text, False
    try:
        serialized = json.dumps(parsed)
    except (TypeError, ValueError):
        serialized = str(parsed)
    if len(serialized) <= _RESULT_SUMMARY_MAX_CHARS:
        return parsed, False
    return _summarize_result(parsed), True


def build_tool_call_result_event(tool_name: str, content: Any) -> dict:
    """Build a ``TOOL_CALL_RESULT`` payload: tool name + result/summary (R16.3)."""
    result, summarized = _result_payload(content)
    payload = {"tool": tool_name, "result": result}
    if summarized:
        payload["summarized"] = True
    return payload


def _extract_error_reason(content: Any) -> str:
    """Best-effort extraction of a human-readable error reason from a result."""
    parsed = _parse_tool_content(content)
    if isinstance(parsed, dict):
        for key in ("error", "error_reason", "message", "detail", "reason"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and not isinstance(value, (dict, list)):
                return str(value)
    text = "" if content is None else (content if isinstance(content, str) else str(content))
    text = text.strip()
    return text[:500] if text else "unknown error"


def tool_result_status(content: Any) -> Tuple[str, Optional[str]]:
    """Classify a tool result as success/failure.

    Returns ``("success", None)`` for a usable result, or
    ``("failure", <error_reason>)`` when the result carries an error marker so
    the ``TOOL_CALL_END`` event can report the failure reason (R16.4, R16.5).
    """
    if _tool_result_is_error(content):
        return "failure", _extract_error_reason(content)
    return "success", None


def build_tool_call_end_event(tool_name: str, status: str, error_reason: Optional[str] = None) -> dict:
    """Build a ``TOOL_CALL_END`` payload: tool name + terminal status (R16.4).

    On a failure status an ``error_reason`` describing the failure is attached
    (R16.5).
    """
    payload = {"tool": tool_name, "status": status}
    if status == "failure" and error_reason:
        payload["error_reason"] = error_reason
    return payload


# ── Verification-step events (R16.6) ─────────────────────────────────────────

def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _outcome_from_volatility(basis: str) -> str:
    """Derive the volatility-stop check outcome from the recorded basis string."""
    b = (basis or "").lower()
    if "< 1.5x atr" in b:
        return "fail"
    if ">= 1.5x atr" in b:
        return "pass"
    if "unavailable" in b:
        return "not-evaluable — ATR unavailable"
    return "informational"


def _outcome_from_macro(statement: str) -> str:
    """Derive the macro-trend-alignment outcome from the recorded statement."""
    s = (statement or "")
    sl = s.lower()
    if sl.startswith("macro conflict"):
        return "fail"
    if sl.startswith("aligned"):
        return "pass"
    if "unavailable" in sl:
        return "not-evaluable — 1D trend unavailable"
    return "informational"


def _regime_step(record: dict) -> dict:
    """Map the defensibility regime entry to a single regime ``VERIFICATION_STEP`` (R8).

    The defensibility record's ``regime`` entry (built by ``graph._regime_entry``)
    is either a usable label — ``{"available": True, "favorability": ..., ...}`` —
    or an Unavailable_Marker — ``{"available": False, "reason": ...}``. The recorded
    Favorability maps to a stable outcome under the fixed check id ``market-regime``:

      * ``favorable``   → ``pass``                       (R8.2)
      * ``unfavorable`` → ``fail``                       (R8.3)
      * ``neutral``     → ``informational``              (R8.4)
      * unavailable     → ``not-evaluable`` (with an 'unavailable' indication, R8.5)

    When the regime is unavailable — no entry, a non-dict entry, ``available`` is
    falsy, or the Favorability is missing/unrecognized — the step reports
    ``not-evaluable`` with an explicit unavailable indication and NEVER substitutes
    a fabricated Favorability (R8.5). Pure; never raises.
    """
    regime = record.get("regime")

    if not isinstance(regime, dict) or not regime.get("available"):
        reason = regime.get("reason") if isinstance(regime, dict) else None
        detail = "Regime unavailable" + (f": {reason}" if reason else "") + "."
        return {
            "check": "market-regime",
            "outcome": "not-evaluable — regime unavailable",
            "detail": detail,
        }

    favorability = regime.get("favorability")
    outcome = {
        "favorable": "pass",
        "unfavorable": "fail",
        "neutral": "informational",
    }.get(favorability)

    if outcome is None:
        # An available entry without a recognized favorability is treated as
        # unavailable rather than fabricating an outcome (R8.5).
        return {
            "check": "market-regime",
            "outcome": "not-evaluable — regime unavailable",
            "detail": "Regime favorability missing or unrecognized.",
        }

    detail = (
        f"favorability={favorability}, trend_state={regime.get('trend_state')}, "
        f"volatility_state={regime.get('volatility_state')}."
    )
    return {"check": "market-regime", "outcome": outcome, "detail": detail}


def _relative_strength_step(record: dict) -> dict:
    """Map the defensibility relative-strength entry to a single ``VERIFICATION_STEP`` (R9).

    The defensibility record's ``relative_strength`` entry (built by
    ``graph._relative_strength_entry``) is either a usable label —
    ``{"available": True, "alignment": ..., "index_direction": ...,
    "relative_strength_state": ..., ...}`` — or an Unavailable_Marker —
    ``{"available": False, "reason": ...}``. The recorded Alignment maps to a
    stable outcome under the fixed check id ``relative-strength``:

      * ``aligned``    → ``pass``                       (R9.2)
      * ``misaligned`` → ``fail``                       (R9.3)
      * ``neutral``    → ``informational``              (R9.4)
      * unavailable    → ``not-evaluable`` (with an 'unavailable' indication, R9.5)

    When relative strength is unavailable — no entry, a non-dict entry,
    ``available`` is falsy, or the Alignment is missing/unrecognized — the step
    reports ``not-evaluable`` with an explicit unavailable indication and NEVER
    substitutes a fabricated Alignment (R9.5). Pure; never raises.
    """
    rs = record.get("relative_strength")

    if not isinstance(rs, dict) or not rs.get("available"):
        reason = rs.get("reason") if isinstance(rs, dict) else None
        detail = "Relative strength unavailable" + (f": {reason}" if reason else "") + "."
        return {
            "check": "relative-strength",
            "outcome": "not-evaluable — relative strength unavailable",
            "detail": detail,
        }

    alignment = rs.get("alignment")
    outcome = {
        "aligned": "pass",
        "misaligned": "fail",
        "neutral": "informational",
    }.get(alignment)

    if outcome is None:
        # An available entry without a recognized alignment is treated as
        # unavailable rather than fabricating an outcome (R9.5).
        return {
            "check": "relative-strength",
            "outcome": "not-evaluable — relative strength unavailable",
            "detail": "Relative-strength alignment missing or unrecognized.",
        }

    detail = (
        f"alignment={alignment}, index_direction={rs.get('index_direction')}, "
        f"relative_strength_state={rs.get('relative_strength_state')}, "
        f"benchmark={rs.get('benchmark')}."
    )
    return {"check": "relative-strength", "outcome": outcome, "detail": detail}


def _forecast_step(record: dict) -> dict:
    """Map the defensibility forecast entry to a single ``VERIFICATION_STEP`` (R10).

    The defensibility record's ``forecast`` entry (built by
    ``graph._forecast_entry``) is either a usable label —
    ``{"available": True, "forecast_alignment": ..., "projected_direction": ...,
    "up_probability": ..., ...}`` — or an Unavailable_Marker —
    ``{"available": False, "reason": ...}``. The recorded Forecast_Alignment maps
    to a stable outcome under the fixed check id ``forecast``:

      * ``aligned``    → ``pass``                       (R10.2)
      * ``misaligned`` → ``fail``                       (R10.3)
      * ``neutral``    → ``informational``              (R10.4)
      * unavailable    → ``not-evaluable`` (with an 'unavailable' indication, R10.5)

    When the forecast is unavailable — no entry, a non-dict entry, ``available``
    is falsy, or the Forecast_Alignment is missing/unrecognized — the step reports
    ``not-evaluable`` with an explicit unavailable indication and NEVER substitutes
    a fabricated alignment (R10.5). Pure; never raises.
    """
    forecast = record.get("forecast")

    if not isinstance(forecast, dict) or not forecast.get("available"):
        reason = forecast.get("reason") if isinstance(forecast, dict) else None
        detail = "Forecast unavailable" + (f": {reason}" if reason else "") + "."
        return {
            "check": "forecast",
            "outcome": "not-evaluable — forecast unavailable",
            "detail": detail,
        }

    alignment = forecast.get("forecast_alignment")
    outcome = {
        "aligned": "pass",
        "misaligned": "fail",
        "neutral": "informational",
    }.get(alignment)

    if outcome is None:
        # An available entry without a recognized alignment is treated as
        # unavailable rather than fabricating an outcome (R10.5).
        return {
            "check": "forecast",
            "outcome": "not-evaluable — forecast unavailable",
            "detail": "Forecast alignment missing or unrecognized.",
        }

    detail = (
        f"forecast_alignment={alignment}, "
        f"projected_direction={forecast.get('projected_direction')}, "
        f"up_probability={forecast.get('up_probability')}, "
        f"expected_move_atr={forecast.get('expected_move_atr')}, "
        f"forecast_confidence={forecast.get('forecast_confidence')}."
    )
    return {"check": "forecast", "outcome": outcome, "detail": detail}


def _trade_management_step(record: dict) -> dict:
    """Map the defensibility management entry to a single ``VERIFICATION_STEP`` (R10).

    The defensibility record's ``management`` entry (built by
    ``graph._management_entry``) is present only for a committed directional
    trade with usable execution levels — it is a dict of the shape
    ``{"available": True, "style": <tm-style>, "action": ..., "entry": ...,
    "initial_stop": ..., "legs": [...], ..., optionally "status": ...}`` — and is
    absent entirely (no ``management`` key) for a HOLD or a decision with no
    usable levels. The recorded management style maps to a stable outcome under
    the fixed check id ``trade-management`` (R10.1):

      * a valid multi-leg / active-management plan (``available`` True and the
        style is anything other than ``single``: ``scale`` / ``scale-be`` /
        ``scale-trail`` / ``scale-be-trail`` / ``be`` / ``trail``)
                                                          -> ``pass``  (R10.2)
      * a Single_Target_Trade (``available`` True, style ``single``)
                                                          -> ``informational`` (R10.3)
      * no management entry present (absent, non-dict, or ``available`` falsy)
                                                          -> ``not-evaluable`` (R10.4)
      * an explicitly invalid plan (the simulated ``status`` is ``"invalid"`` —
        e.g. a zero initial-stop distance) -> ``fail`` (the only representable
        failed-plan state; otherwise the three cases above cover everything)

    The outcome string may carry a short suffix mirroring the sibling steps, but
    the check id is always exactly ``trade-management`` and the primary outcome
    token is one of ``pass`` / ``fail`` / ``informational`` / ``not-evaluable``.
    Pure; never raises.
    """
    management = record.get("management")

    if not isinstance(management, dict) or not management.get("available"):
        reason = management.get("reason") if isinstance(management, dict) else None
        detail = "No management plan recorded" + (f": {reason}" if reason else "") + "."
        return {
            "check": "trade-management",
            "outcome": "not-evaluable — no management plan",
            "detail": detail,
        }

    style = management.get("style")

    # An explicitly invalid simulated plan (e.g. zero initial-stop distance) is
    # the only representable failed-plan state.
    if management.get("status") == "invalid":
        return {
            "check": "trade-management",
            "outcome": f"fail — invalid plan ({style})",
            "detail": "Trade_Manager reported the plan as invalid for scoring.",
        }

    # A Single_Target_Trade collapses to the ``single`` style: no active
    # management, surfaced as informational (R10.3).
    if style == "single":
        return {
            "check": "trade-management",
            "outcome": "informational — single-target (no active management)",
            "detail": (
                f"action={management.get('action')}, entry={management.get('entry')}, "
                f"initial_stop={management.get('initial_stop')}."
            ),
        }

    # An available entry without a recognized active style is treated as
    # not-evaluable rather than fabricating a pass (defensive; ``unknown`` only
    # arises when no plan was built, which an available entry never is).
    if not style or style == "unknown":
        return {
            "check": "trade-management",
            "outcome": "not-evaluable — management style unrecognized",
            "detail": "Management entry present without a recognized management style.",
        }

    # A valid multi-leg / active-management plan (R10.2).
    legs = management.get("legs") or []
    detail = (
        f"style={style}, action={management.get('action')}, legs={len(legs)}, "
        f"breakeven={'yes' if management.get('breakeven') else 'no'}, "
        f"trailing={'yes' if management.get('trailing') else 'no'}."
    )
    return {"check": "trade-management", "outcome": f"pass — managed plan ({style})", "detail": detail}


def _session_step(record: dict) -> dict:
    """Map the defensibility session entry to a single ``VERIFICATION_STEP`` (R9).

    The defensibility record's ``session`` entry (built by ``graph._session_entry``)
    is either a usable label — ``{"available": True, "session_phase": ...,
    "time_favorability": ..., "expiry_context": {...}, "minutes_since_open": ...,
    "minutes_until_close": ...}`` — or an Unavailable_Marker —
    ``{"available": False, "reason": ...}``. The recorded Time_Favorability maps
    to a stable outcome under the fixed check id ``session``:

      * ``favorable``   → ``pass``                       (R9.2)
      * ``unfavorable`` → ``fail``                       (R9.3)
      * ``neutral``     → ``informational``              (R9.4)
      * unavailable     → ``not-evaluable`` (with an 'unavailable' indication, R9.5)

    When the session context is unavailable — no entry, a non-dict entry,
    ``available`` is falsy, or the Time_Favorability is missing/unrecognized — the
    step reports ``not-evaluable`` with an explicit unavailable indication and
    NEVER substitutes a fabricated favorability (R9.5). Pure; never raises.
    """
    session = record.get("session")

    if not isinstance(session, dict) or not session.get("available"):
        reason = session.get("reason") if isinstance(session, dict) else None
        detail = "Session context unavailable" + (f": {reason}" if reason else "") + "."
        return {
            "check": "session",
            "outcome": "not-evaluable — session context unavailable",
            "detail": detail,
        }

    favorability = session.get("time_favorability")
    outcome = {
        "favorable": "pass",
        "unfavorable": "fail",
        "neutral": "informational",
    }.get(favorability)

    if outcome is None:
        # An available entry without a recognized favorability is treated as
        # unavailable rather than fabricating an outcome (R9.5).
        return {
            "check": "session",
            "outcome": "not-evaluable — session context unavailable",
            "detail": "Session time_favorability missing or unrecognized.",
        }

    expiry = session.get("expiry_context")
    if isinstance(expiry, dict):
        expiry_detail = (
            f"is_expiry_day={expiry.get('is_expiry_day')}, "
            f"days_until_expiry={expiry.get('days_until_expiry')}"
        )
    else:
        expiry_detail = "expiry_context unavailable"
    detail = (
        f"time_favorability={favorability}, "
        f"session_phase={session.get('session_phase')}, "
        f"minutes_since_open={session.get('minutes_since_open')}, "
        f"minutes_until_close={session.get('minutes_until_close')}, "
        f"{expiry_detail}."
    )
    return {"check": "session", "outcome": outcome, "detail": detail}


def _options_step(record: dict) -> dict:
    """Map the defensibility options entry to a single ``VERIFICATION_STEP`` (R7).

    The defensibility record's ``options`` entry (built by ``graph._options_entry``)
    is either a usable Options_Bias_Label —
    ``{"available": True, "options_bias_state": ..., "alignment": ...,
    "chain_context": ..., "pcr_oi": ..., "max_pain": ..., "oi_walls": {...}, ...}``
    — or an Unavailable_Marker — ``{"available": False, "reason": ...}``. The
    recorded Alignment maps to a stable outcome under the fixed check id
    ``options``:

      * ``aligned``    → ``pass``                       (R7.2)
      * ``misaligned`` → ``fail``                       (R7.2)
      * ``neutral``    → ``informational``              (R7.2)
      * unavailable    → ``not-evaluable`` (with an 'unavailable' indication, R7.3)

    When options context is unavailable — no entry, a non-dict entry,
    ``available`` is falsy, or the Alignment is missing/unrecognized — the step
    reports ``not-evaluable`` with an explicit unavailable indication and NEVER
    substitutes a fabricated alignment (R7.3). Pure; never raises.
    """
    options = record.get("options")

    if not isinstance(options, dict) or not options.get("available"):
        reason = options.get("reason") if isinstance(options, dict) else None
        detail = "Options context unavailable" + (f": {reason}" if reason else "") + "."
        return {
            "check": "options",
            "outcome": "not-evaluable — options unavailable",
            "detail": detail,
        }

    alignment = options.get("alignment")
    outcome = {
        "aligned": "pass",
        "misaligned": "fail",
        "neutral": "informational",
    }.get(alignment)

    if outcome is None:
        # An available entry without a recognized alignment is treated as
        # unavailable rather than fabricating an outcome (R7.3).
        return {
            "check": "options",
            "outcome": "not-evaluable — options unavailable",
            "detail": "Options alignment missing or unrecognized.",
        }

    oi_walls = options.get("oi_walls")
    if isinstance(oi_walls, dict):
        walls_detail = (
            f"support={oi_walls.get('support')}, resistance={oi_walls.get('resistance')}"
        )
    else:
        walls_detail = "oi_walls unavailable"
    detail = (
        f"alignment={alignment}, "
        f"options_bias_state={options.get('options_bias_state')}, "
        f"pcr_oi={options.get('pcr_oi')}, max_pain={options.get('max_pain')}, "
        f"{walls_detail}, chain_context={options.get('chain_context')}."
    )
    return {"check": "options", "outcome": outcome, "detail": detail}


def _event_step(record: dict) -> dict:
    """Map the defensibility event entry to a single event-risk ``VERIFICATION_STEP`` (R9).

    The defensibility record's ``event`` entry (built by ``graph._event_entry``)
    is either a usable Event_Assessment — ``{"available": True, "event_risk": ...,
    "days_until_event": ..., "event_date": ..., "event_recommendation": ...}`` —
    or an Unavailable_Marker — ``{"available": False, "reason": ...}``. The
    recorded Event_Risk maps to a stable outcome under the fixed check id
    ``event-risk``:

      * ``clear``         → ``pass``                       (R9.2)
      * ``through_event`` → ``fail``                       (R9.3)
      * ``imminent``      → ``informational``              (R9.4)
      * unavailable       → ``not-evaluable`` (with an 'unavailable' indication, R9.5)

    When the event context is unavailable — no entry, a non-dict entry,
    ``available`` is falsy, or the Event_Risk is missing/unrecognized — the step
    reports ``not-evaluable`` with an explicit unavailable indication and NEVER
    substitutes a fabricated risk (R9.5). Scheduled-event risk is a filter /
    defensibility surface only, so the step never blocks or overrides a decision.
    Pure; never raises.
    """
    event = record.get("event")

    if not isinstance(event, dict) or not event.get("available"):
        reason = event.get("reason") if isinstance(event, dict) else None
        detail = "Event risk unavailable" + (f": {reason}" if reason else "") + "."
        return {
            "check": "event-risk",
            "outcome": "not-evaluable — event risk unavailable",
            "detail": detail,
        }

    event_risk = event.get("event_risk")
    outcome = {
        "clear": "pass",
        "through_event": "fail",
        "imminent": "informational",
    }.get(event_risk)

    if outcome is None:
        # An available entry without a recognized event_risk is treated as
        # unavailable rather than fabricating an outcome (R9.5).
        return {
            "check": "event-risk",
            "outcome": "not-evaluable — event risk unavailable",
            "detail": "Event risk missing or unrecognized.",
        }

    detail = (
        f"event_risk={event_risk}, "
        f"days_until_event={event.get('days_until_event')}, "
        f"event_date={event.get('event_date')}, "
        f"event_recommendation={event.get('event_recommendation')}."
    )
    held = event.get("trade_held_through_event")
    if held:
        detail += f" {held}"
    return {"check": "event-risk", "outcome": outcome, "detail": detail}


def _debate_consensus_step(record: dict) -> dict:
    """Map the defensibility debate entry to a single debate-consensus ``VERIFICATION_STEP`` (R8).

    The defensibility record's ``debate`` entry (built by ``graph`` for a
    DEBATE-mode decision, task 11.1) is a dict of the shape
    ``{"bull_stance": ..., "bear_stance": ..., "consensus": ...,
    "conviction": ..., "conviction_basis": ...,
    optionally "committed_against_contested": ...}``, and is **absent entirely**
    for any non-DEBATE run. The recorded Debate_Consensus maps to a stable
    outcome under the fixed check id ``debate-consensus`` (R8.2):

      * ``strong_agree`` → ``pass``                       (R8.3)
      * ``lean``         → ``informational``              (R8.3)
      * ``contested``    → ``fail``                       (R8.3)
      * no debate entry / missing / unrecognized consensus → ``not-evaluable`` (R8.4)

    When no debate entry is present — no ``debate`` key, a non-dict entry, or the
    consensus is missing/unrecognized — the step reports ``not-evaluable`` and
    NEVER substitutes a fabricated consensus (R8.4). The check id is always
    exactly ``debate-consensus`` so a DEBATE run surfaces exactly one such step
    (R8.1/R8.2). Pure; never raises.
    """
    debate = record.get("debate")

    if not isinstance(debate, dict):
        return {
            "check": "debate-consensus",
            "outcome": "not-evaluable — no debate entry",
            "detail": "No debate entry present in the defensibility record.",
        }

    consensus = debate.get("consensus")
    outcome = {
        "strong_agree": "pass",
        "lean": "informational",
        "contested": "fail",
    }.get(consensus)

    if outcome is None:
        # A debate entry without a recognized consensus is treated as
        # not-evaluable rather than fabricating an outcome (R8.4).
        return {
            "check": "debate-consensus",
            "outcome": "not-evaluable — debate consensus unrecognized",
            "detail": f"Debate consensus missing or unrecognized (consensus={consensus!r}).",
        }

    detail = f"consensus={consensus}, conviction={debate.get('conviction')}"
    basis = debate.get("conviction_basis")
    if basis:
        detail += f", basis={basis}"
    committed_against_contested = debate.get("committed_against_contested")
    if committed_against_contested:
        detail += f"; {committed_against_contested}"
    detail += "."
    return {"check": "debate-consensus", "outcome": outcome, "detail": detail}


def _derive_find_mode_steps(record: dict) -> List[dict]:
    """Derive the four self-verification checks from a FIND-mode record (R16.6).

    The defensibility record assembled in ``graph.py`` already holds the inputs
    for the risk-manager protocol checks: the Risk_Reward_Ratio, the volatility
    basis for the stop, the macro-trend-conflict statement, and the
    support/resistance levels used. Each is mapped to a named check + outcome.
    """
    steps: List[dict] = []

    rr = record.get("risk_reward")
    if _is_number(rr):
        steps.append({
            "check": "risk-reward",
            "outcome": "pass" if rr >= 2.0 else "fail",
            "detail": f"RR={rr}",
        })
    else:
        steps.append({"check": "risk-reward", "outcome": "not-evaluable — RR unavailable"})

    volatility_basis = record.get("volatility_basis") or ""
    steps.append({
        "check": "volatility-stop",
        "outcome": _outcome_from_volatility(volatility_basis),
        "detail": volatility_basis,
    })

    macro = record.get("macro_trend_conflict") or ""
    steps.append({
        "check": "macro-trend-alignment",
        "outcome": _outcome_from_macro(macro),
        "detail": macro,
    })

    sr = record.get("support_resistance")
    levels = record.get("levels")
    if sr and levels:
        steps.append({
            "check": "level-alignment",
            "outcome": "pass",
            "detail": "Entry/stop placed against support_resistance levels.",
        })
    elif sr:
        steps.append({
            "check": "level-alignment",
            "outcome": "informational",
            "detail": "Support/resistance levels available.",
        })
    else:
        steps.append({"check": "level-alignment", "outcome": "not-evaluable — S/R unavailable"})

    # ── Volume Profile auction check (Phase 1 evidence) ──────────────────────
    vp = record.get("volume_profile")
    if isinstance(vp, dict) and vp.get("poc") is not None:
        loc = str(vp.get("price_vs_value_area") or "unknown").replace("_", " ")
        steps.append({
            "check": "volume-profile",
            "outcome": "informational",
            "detail": f"POC={vp.get('poc')}, VAH={vp.get('vah')}, VAL={vp.get('val')}; price {loc}.",
        })
    else:
        steps.append({"check": "volume-profile", "outcome": "not-evaluable — volume profile unavailable"})

    # ── Track-record calibration check (Phase 2 feedback loop) ───────────────
    tr = record.get("track_record")
    if isinstance(tr, dict) and isinstance(tr.get("overall"), dict):
        ov = tr["overall"]
        scored = ov.get("trades_scored")
        exp = ov.get("expectancy_r")
        wr = ov.get("win_rate")
        if scored:
            if _is_number(exp):
                outcome = "pass" if exp > 0 else "fail"
            else:
                outcome = "informational"
            low = " (low sample — weak prior)" if tr.get("low_sample") else ""
            steps.append({
                "check": "track-record",
                "outcome": outcome,
                "detail": f"win_rate={wr}, expectancy_r={exp} over {scored} scored trade(s){low}.",
            })
        else:
            steps.append({
                "check": "track-record",
                "outcome": "informational",
                "detail": "No scored trades yet — no realized edge to calibrate against.",
            })
    else:
        steps.append({"check": "track-record", "outcome": "not-evaluable — track record unavailable"})

    # ── Market-regime gate check (regime-detection-gate, R8) ─────────────────
    # Exactly one regime step, derived from the defensibility regime entry.
    steps.append(_regime_step(record))

    # ── Relative-strength context check (relative-strength-context, R9) ──────
    # Exactly one relative-strength step, derived from the defensibility
    # relative-strength entry.
    steps.append(_relative_strength_step(record))

    # ── Forecast verification check (volatility-aware-forecaster, R10) ───────
    # Exactly one forecast step, derived from the defensibility forecast entry.
    steps.append(_forecast_step(record))

    # ── Trade-management verification check (trade-management, R10) ──────────
    # Exactly one trade-management step, derived from the defensibility
    # management entry (absent -> not-evaluable).
    steps.append(_trade_management_step(record))

    # ── Session & expiry awareness check (session-expiry-awareness, R9) ──────
    # Exactly one session step, derived from the defensibility session entry.
    steps.append(_session_step(record))

    # ── Options positioning context check (options-agent-integration, R7) ────
    # Exactly one options step, derived from the defensibility options entry
    # (unavailable -> not-evaluable). Appended among the sibling context steps,
    # before the debate-consensus step, so it is ordered before the DECISION
    # event by `decision_events` (R7.4).
    steps.append(_options_step(record))

    # ── Scheduled-event risk check (earnings-event-risk-gate, R9) ────────────
    # Exactly one event-risk step, derived from the defensibility event entry
    # (unavailable -> not-evaluable). Appended among the sibling context steps so
    # it is ordered before the DECISION event by `decision_events` (R9.6).
    steps.append(_event_step(record))

    # ── Debate-consensus check (multi-agent-debate, R8) ──────────────────────
    # Exactly one debate-consensus step, derived from the defensibility debate
    # entry (absent for non-DEBATE runs -> not-evaluable). Appended last so it is
    # ordered before the DECISION event by `decision_events` (R8.5).
    steps.append(_debate_consensus_step(record))

    return steps


def build_verification_steps(decision: Any) -> List[dict]:
    """Build the ordered ``VERIFICATION_STEP`` payloads for a decision (R16.6).

    In VERIFY mode the defensibility record already carries an explicit
    per-check outcome list (``validator_checks``, R7.4); those are surfaced
    verbatim. In FIND mode the four self-verification checks are derived from the
    record's recorded evidence. Each returned payload names the check and states
    its outcome.
    """
    if not isinstance(decision, dict):
        return []
    record = decision.get("defensibility")
    if not isinstance(record, dict):
        return []

    checks = record.get("validator_checks")
    if isinstance(checks, list) and checks:
        steps: List[dict] = []
        for c in checks:
            if isinstance(c, dict) and c.get("check"):
                step = {"check": c["check"], "outcome": c.get("outcome", "n/a")}
                if c.get("detail"):
                    step["detail"] = c["detail"]
                steps.append(step)
        # Surface exactly one regime step in VERIFY mode too: append the derived
        # regime step only when the validator checks don't already carry one
        # (R8.1 — exactly one regime VERIFICATION_STEP).
        if not any(s.get("check") == "market-regime" for s in steps):
            steps.append(_regime_step(record))
        # Surface exactly one relative-strength step in VERIFY mode too: append
        # the derived step only when the validator checks don't already carry one
        # (R9.1 — exactly one relative-strength VERIFICATION_STEP).
        if not any(s.get("check") == "relative-strength" for s in steps):
            steps.append(_relative_strength_step(record))
        # Surface exactly one forecast step in VERIFY mode too: append the
        # derived step only when the validator checks don't already carry one
        # (R10.1 — exactly one forecast VERIFICATION_STEP).
        if not any(s.get("check") == "forecast" for s in steps):
            steps.append(_forecast_step(record))
        # Surface exactly one trade-management step in VERIFY mode too: append
        # the derived step only when the validator checks don't already carry one
        # (R10.1 — exactly one trade-management VERIFICATION_STEP).
        if not any(s.get("check") == "trade-management" for s in steps):
            steps.append(_trade_management_step(record))
        # Surface exactly one session step in VERIFY mode too: append the
        # derived step only when the validator checks don't already carry one
        # (R9.1 — exactly one session VERIFICATION_STEP).
        if not any(s.get("check") == "session" for s in steps):
            steps.append(_session_step(record))
        # Surface exactly one options step in VERIFY mode too: append the
        # derived step only when the validator checks don't already carry one
        # (options-agent-integration R7.1 — exactly one options VERIFICATION_STEP).
        if not any(s.get("check") == "options" for s in steps):
            steps.append(_options_step(record))
        # Surface exactly one event-risk step in VERIFY mode too: append the
        # derived step only when the validator checks don't already carry one
        # (earnings-event-risk-gate R9.1 — exactly one event-risk VERIFICATION_STEP).
        if not any(s.get("check") == "event-risk" for s in steps):
            steps.append(_event_step(record))
        # Surface exactly one debate-consensus step in VERIFY mode too: append
        # the derived step only when the validator checks don't already carry one
        # (multi-agent-debate R8.1 — exactly one debate-consensus VERIFICATION_STEP).
        if not any(s.get("check") == "debate-consensus" for s in steps):
            steps.append(_debate_consensus_step(record))
        return steps

    return _derive_find_mode_steps(record)


# ── Decision event (R16.7) ───────────────────────────────────────────────────

def build_decision_event(decision: Any) -> Optional[dict]:
    """Build a ``DECISION`` payload: action + conviction score + rationale (R16.7).

    Returns ``None`` when ``decision`` is not a structured decision dict. The
    rationale prefers the ``setup_validation`` synthesis and falls back to the
    forced/gated ``reason`` so a HOLD always carries a rationale.
    """
    if not isinstance(decision, dict):
        return None
    payload = {
        "action": decision.get("action"),
        "conviction_score": decision.get("conviction_score"),
        "rationale": decision.get("setup_validation") or decision.get("reason"),
        # Carry the execution plan so the UI can populate the trade card directly
        # from the stream (in addition to the rationale).
        "execution_plan": decision.get("execution_plan"),
    }
    # Carry the Adaptive Opportunity Engine tier + size factor when present so the
    # UI can badge the tier and the telemetry tee can record it (R9.2, R9.3). A
    # decision from a run without the engine simply omits these (kept as null-free
    # additions only when actually stamped).
    tier = decision.get("opportunity_tier")
    if isinstance(tier, str) and tier:
        payload["opportunity_tier"] = tier
    if "size_factor" in decision:
        payload["size_factor"] = decision.get("size_factor")
    return payload


# ── Run-lifecycle event builders (R17.1, R17.2, R17.5, R17.6) ────────────────

def build_run_started_event(thread_id: Any) -> dict:
    """Build the ``RUN_STARTED`` payload — the first event of every run (R17.1)."""
    return {"thread_id": thread_id}


def build_run_finished_event(thread_id: Any, status: str) -> dict:
    """Build the terminal ``RUN_FINISHED`` payload (R17.2, R17.6).

    ``status`` is normalized to the documented set ``{"completed", "paused"}`` so
    the user interface can always distinguish a paused run from a completed one
    (R17.6); any unexpected value is treated as ``"completed"``.
    """
    normalized = status if status in (RUN_COMPLETED, RUN_PAUSED) else RUN_COMPLETED
    return {"thread_id": thread_id, "status": normalized}


def build_error_event(detail: Any) -> dict:
    """Build the ``ERROR`` payload for a failed LLM stream (R17.5, R5.5).

    Surfaces a clean analysis-unavailable error so the UI can prompt a retry —
    no fabricated trade plan is ever emitted.
    """
    return {"error": f"AI analysis unavailable: {detail}"}


# ── Per-message / per-decision event expansion ───────────────────────────────

def message_events(msg: Any) -> Iterator[Tuple[str, dict]]:
    """Yield ordered ``(event_name, payload)`` tuples for a single graph message.

    Implements the per-message slice of the event vocabulary (Requirement 16):

      * ``AIMessage``   → ``REASONING`` (markup stripped, R16.1/R16.8) followed by
                          a ``TOOL_CALL_START`` for each issued tool call (R16.2).
      * ``ToolMessage`` → ``TOOL_CALL_RESULT`` (R16.3) followed by ``TOOL_CALL_END``
                          carrying a success/failure status and an ``error_reason``
                          on failure (R16.4, R16.5).

    Reasoning is emitted before the tool-call markers so the observed order
    mirrors "think out loud, then act". Tuples (not SSE strings) are yielded so
    :func:`assemble_run_events` and the ordering property tests can inspect the
    event sequence directly; ``main.py`` frames them with :func:`format_sse`.
    """
    msg_type = type(msg).__name__

    if "ToolMessage" in msg_type:
        tool_name = getattr(msg, "name", None) or "tool"
        content = getattr(msg, "content", None)
        yield TOOL_CALL_RESULT, build_tool_call_result_event(tool_name, content)
        status, error_reason = tool_result_status(content)
        yield TOOL_CALL_END, build_tool_call_end_event(tool_name, status, error_reason)
        return

    if "AIMessage" in msg_type:
        # Surface a `role` tag (bull/bear/judge) when the source AIMessage carries
        # one in additional_kwargs so the debate role reasoning events are
        # distinguishable (multi-agent-debate R8.1). Single-agent messages carry
        # no role tag, so the payload shape is unchanged for them.
        role = None
        kwargs = getattr(msg, "additional_kwargs", None)
        if isinstance(kwargs, dict):
            tag = kwargs.get("role")
            if isinstance(tag, str) and tag.strip():
                role = tag.strip()
        reasoning = build_reasoning_event(getattr(msg, "content", None), role)
        if reasoning is not None:
            yield REASONING, reasoning
        for tc in getattr(msg, "tool_calls", None) or []:
            yield TOOL_CALL_START, build_tool_call_start_event(tc.get("name"), tc.get("args"))


def build_best_current_read_event(read: Any) -> Optional[dict]:
    """Build a ``BEST_CURRENT_READ`` payload (adaptive-opportunity-engine R8).

    The interim, non-committal assessment surfaced on a stand_aside / HOLD: the
    current directional ``bias``, the reference ``levels``, and ``why_standing_aside``.
    Returns ``None`` when ``read`` is not a structured read dict. It is NEVER a
    committed trade — it carries no action / conviction / execution plan (R8.3), so
    only the three assessment fields are surfaced regardless of any extra keys.
    """
    if not isinstance(read, dict):
        return None
    levels = read.get("levels")
    return {
        "bias": read.get("bias"),
        "levels": levels if isinstance(levels, dict) else {},
        "why_standing_aside": read.get("why_standing_aside"),
    }


def decision_events(decision: Any) -> Iterator[Tuple[str, dict]]:
    """Yield ``VERIFICATION_STEP`` tuples, then ``BEST_CURRENT_READ`` on a
    stand-aside, then the ``DECISION`` tuple (R16.6, R16.7, R8.1/R8.4).

    Verification steps precede the decision so the observed order reflects the
    self-verification protocol running before the trade is finalized. A
    stand_aside / HOLD decision carrying a ``best_current_read`` surfaces it (as an
    assessment, ordered before the DECISION) so the trader gets an actionable read
    even when no trade is taken.
    """
    for step in build_verification_steps(decision):
        yield VERIFICATION_STEP, step
    if isinstance(decision, dict):
        read_event = build_best_current_read_event(decision.get("best_current_read"))
        if read_event is not None:
            yield BEST_CURRENT_READ, read_event
    decision_event = build_decision_event(decision)
    if decision_event is not None:
        yield DECISION, decision_event


def node_update_events(node_data: Any) -> Iterator[Tuple[str, dict]]:
    """Expand one LangGraph node update into ordered event tuples.

    Messages are surfaced first (reasoning + tool markers, in message order),
    then a standalone interim ``best_current_read`` (surfaced on a heartbeat pulse
    while the agent keeps waiting — Requirement 8.2), then any committed/forced
    ``decision`` surfaces its ``VERIFICATION_STEP`` and ``DECISION`` events (R16.6,
    R16.7). Non-dict updates yield nothing.
    """
    if not isinstance(node_data, dict):
        return
    for msg in node_data.get("messages") or []:
        yield from message_events(msg)
    decision = node_data.get("decision")
    # A standalone Best_Current_Read on an update WITHOUT a committed decision is a
    # mid-wait heartbeat read (R8.2). When a decision IS present its own
    # ``best_current_read`` is surfaced by ``decision_events`` below, so we skip the
    # standalone emit here to avoid a duplicate card.
    if not decision:
        read = node_data.get("best_current_read")
        if read:
            read_event = build_best_current_read_event(read)
            if read_event is not None:
                yield BEST_CURRENT_READ, read_event
    if decision:
        yield from decision_events(decision)


# ── Ordered run assembly (R17.1-R17.6) ───────────────────────────────────────

def assemble_run_events(
    thread_id: Any,
    node_updates: Any,
    outcome: str,
    error_detail: Any = None,
) -> List[Tuple[str, dict]]:
    """Assemble the full, ordered ``(event_name, payload)`` list for one run.

    This is the pure model of the live ordering enforced by ``main.py``'s
    ``event_generator`` and is what the ordering/resilience property tests
    (tasks 15.10-15.14) target:

      * ``RUN_STARTED`` is always emitted first (R17.1).
      * Each node update is expanded in arrival order, preserving step order and
        the ``TOOL_CALL_START`` → ``RESULT`` → ``END`` sequence per tool (R17.3,
        R17.4).
      * A run that completes or pauses ends with exactly one ``RUN_FINISHED``
        carrying its ``completed``/``paused`` status as the final event (R17.2,
        R17.6).
      * A run whose LLM stream failed ends with an ``ERROR`` event and emits **no**
        ``DECISION`` event for that run (R17.5, R5.5); reasoning/tool events that
        genuinely occurred before the failure are retained.

    ``outcome`` is one of ``"completed"``, ``"paused"``, or ``"error"``.
    """
    events: List[Tuple[str, dict]] = [(RUN_STARTED, build_run_started_event(thread_id))]

    is_error = outcome == RUN_ERROR
    for node_data in node_updates or []:
        for name, payload in node_update_events(node_data):
            # A failed stream must never surface a DECISION for the run (R17.5).
            if is_error and name == DECISION:
                continue
            events.append((name, payload))

    if is_error:
        events.append((ERROR, build_error_event(error_detail)))
    else:
        events.append((RUN_FINISHED, build_run_finished_event(thread_id, outcome)))
    return events
