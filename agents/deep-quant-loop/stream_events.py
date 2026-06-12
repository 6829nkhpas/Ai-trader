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


def build_reasoning_event(content: Any) -> Optional[dict]:
    """Build a ``REASONING`` payload from an assistant message's content.

    Strips all tool-call markup (R16.8) and returns ``{"content": <text>}`` only
    when natural-language reasoning remains; returns ``None`` when the content
    was empty or consisted solely of markup, so the caller emits no event for it
    (R16.1).
    """
    stripped = strip_tool_call_markup(content)
    if not stripped:
        return None
    return {"content": stripped}


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
    return {
        "action": decision.get("action"),
        "conviction_score": decision.get("conviction_score"),
        "rationale": decision.get("setup_validation") or decision.get("reason"),
        # Carry the execution plan so the UI can populate the trade card directly
        # from the stream (in addition to the rationale).
        "execution_plan": decision.get("execution_plan"),
    }


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
        reasoning = build_reasoning_event(getattr(msg, "content", None))
        if reasoning is not None:
            yield REASONING, reasoning
        for tc in getattr(msg, "tool_calls", None) or []:
            yield TOOL_CALL_START, build_tool_call_start_event(tc.get("name"), tc.get("args"))


def decision_events(decision: Any) -> Iterator[Tuple[str, dict]]:
    """Yield ``VERIFICATION_STEP`` tuples then the ``DECISION`` tuple (R16.6, R16.7).

    Verification steps precede the decision so the observed order reflects the
    self-verification protocol running before the trade is finalized.
    """
    for step in build_verification_steps(decision):
        yield VERIFICATION_STEP, step
    decision_event = build_decision_event(decision)
    if decision_event is not None:
        yield DECISION, decision_event


def node_update_events(node_data: Any) -> Iterator[Tuple[str, dict]]:
    """Expand one LangGraph node update into ordered event tuples.

    Messages are surfaced first (reasoning + tool markers, in message order),
    then any committed/forced ``decision`` surfaces its ``VERIFICATION_STEP`` and
    ``DECISION`` events (R16.6, R16.7). Non-dict updates yield nothing.
    """
    if not isinstance(node_data, dict):
        return
    for msg in node_data.get("messages") or []:
        yield from message_events(msg)
    decision = node_data.get("decision")
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
