"""Property- and example-based tests for the glass-box SSE stream helpers.

Feature: deep-quant-analysis-hardening

Covers tasks 15.3-15.15 (design Properties 53-64 plus the end-to-end
stream-failure unit test) against the pure helpers in ``stream_events.py``:

  * the reasoning splitter / markup stripping        (Properties 53, 59)
  * the per-event payload builders                   (Properties 54-58)
  * the ordered run assembler + lifecycle/ERROR path (Properties 60-64)

The LLM and the graph are never invoked. Lightweight stub message objects whose
class names contain ``AIMessage``/``ToolMessage`` stand in for the LangChain
messages (``message_events`` dispatches on ``type(msg).__name__``). All tests use
Hypothesis with ``max_examples=100`` except task 15.15, an example-based unit
test.
"""

import json
import os
import sys

from hypothesis import given, settings, strategies as st

# Make the service package importable (stream_events.py / graph.py live up one).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import stream_events as se  # noqa: E402
from stream_events import (  # noqa: E402
    RUN_STARTED,
    RUN_FINISHED,
    ERROR,
    REASONING,
    TOOL_CALL_START,
    TOOL_CALL_RESULT,
    TOOL_CALL_END,
    VERIFICATION_STEP,
    DECISION,
    RUN_COMPLETED,
    RUN_PAUSED,
    RUN_ERROR,
    ensure_json_object,
    format_sse,
    strip_tool_call_markup,
    build_reasoning_event,
    build_tool_call_start_event,
    build_tool_call_result_event,
    build_tool_call_end_event,
    build_verification_steps,
    build_decision_event,
    message_events,
    decision_events,
    node_update_events,
    assemble_run_events,
    _BEGIN_TOKEN,
    _SEP_TOKEN,
    _END_TOKEN,
)

SETTINGS = settings(max_examples=100)


# ── Lightweight stub message objects ─────────────────────────────────────────
# ``message_events`` dispatches on ``type(msg).__name__`` containing the strings
# "AIMessage" / "ToolMessage", so these stub class names are deliberately chosen
# to match.
class StubAIMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"


class StubToolMessage:
    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


# ── Shared strategies ────────────────────────────────────────────────────────

# Plain natural-language text that contains NO custom-token markup. The markup
# tokens use fullwidth ``｜`` (U+FF5C) and ``▁`` (U+2581); restricting to printable
# ASCII guarantees no token can appear by chance.
nl_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=120,
).filter(lambda s: s.strip())

TOOL_NAMES = [
    "get_candles",
    "get_consensus_report",
    "get_multi_tf_trend",
    "get_chart_patterns",
    "get_support_resistance",
    "get_news_context",
    "watch_price_condition",
    "declare_trade",
]
tool_name = st.sampled_from(TOOL_NAMES)

# JSON-object args for a tool call.
json_args = st.dictionaries(
    keys=st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8),
    values=st.one_of(
        st.integers(min_value=-1000, max_value=1000),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
        st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=10),
        st.booleans(),
    ),
    max_size=5,
)


def _tool_call(name, args):
    return {"name": name, "args": args, "id": f"call_{name}"}


# Result content for a ToolMessage that represents a usable (non-error) payload.
usable_result_content = st.builds(
    lambda d: json.dumps(d),
    st.dictionaries(
        keys=st.sampled_from(["trend_score", "current_price", "rsi_14", "pivot"]),
        values=st.floats(allow_nan=False, allow_infinity=False, min_value=-1e4, max_value=1e4),
        min_size=1,
        max_size=4,
    ),
)

# Result content carrying an error marker (recognized by ``_tool_result_is_error``).
error_result_content = st.sampled_from(
    [
        '{"error": "Failed to retrieve candles: timeout"}',
        "{'error': 'connection refused'}",
        "error: upstream unavailable",
        '{"symbol": "X", "error": "no data"}',
    ]
)


# ── 15.3 / Property 53 ───────────────────────────────────────────────────────
@given(content=nl_text)
@SETTINGS
def test_property_53_reasoning_only_emits_reasoning(content):
    """Feature: deep-quant-analysis-hardening, Property 53: Reasoning-only messages emit a REASONING event"""
    msg = StubAIMessage(content=content, tool_calls=[])
    events = list(message_events(msg))

    # Exactly one REASONING event, no tool-call markers, and its content is the
    # markup-stripped natural-language reasoning (which is non-empty here).
    reasoning = [(n, p) for n, p in events if n == REASONING]
    assert len(reasoning) == 1
    assert all(n != TOOL_CALL_START for n, _ in events)
    payload = reasoning[0][1]
    assert payload["content"] == strip_tool_call_markup(content)
    assert payload["content"].strip()


# ── 15.4 / Property 54 ───────────────────────────────────────────────────────
@given(calls=st.lists(st.tuples(tool_name, json_args), min_size=1, max_size=5))
@SETTINGS
def test_property_54_tool_calls_emit_start(calls):
    """Feature: deep-quant-analysis-hardening, Property 54: Tool calls emit START with name and args"""
    tool_calls = [_tool_call(n, a) for n, a in calls]
    msg = StubAIMessage(content="", tool_calls=tool_calls)
    starts = [p for n, p in message_events(msg) if n == TOOL_CALL_START]

    assert len(starts) == len(calls)
    for (name, args), payload in zip(calls, starts):
        assert payload["tool"] == name
        # Args are surfaced verbatim (None coerced to an empty object).
        assert payload["args"] == (args if args is not None else {})


@given(name=tool_name, args=st.one_of(json_args, st.none()))
@SETTINGS
def test_property_54_start_builder_shape(name, args):
    """Feature: deep-quant-analysis-hardening, Property 54: Tool calls emit START with name and args"""
    payload = build_tool_call_start_event(name, args)
    assert payload["tool"] == name
    assert payload["args"] == (args if args is not None else {})


# ── 15.5 / Property 55 ───────────────────────────────────────────────────────
@given(name=tool_name, content=usable_result_content)
@SETTINGS
def test_property_55_tool_results_emit_result(name, content):
    """Feature: deep-quant-analysis-hardening, Property 55: Tool results emit RESULT with name and result/summary"""
    msg = StubToolMessage(content=content, name=name)
    results = [p for n, p in message_events(msg) if n == TOOL_CALL_RESULT]

    assert len(results) == 1
    payload = results[0]
    assert payload["tool"] == name
    # Either the verbatim result or a structured summary is present.
    assert "result" in payload


@given(name=tool_name, content=st.one_of(usable_result_content, error_result_content))
@SETTINGS
def test_property_55_result_builder_carries_name(name, content):
    """Feature: deep-quant-analysis-hardening, Property 55: Tool results emit RESULT with name and result/summary"""
    payload = build_tool_call_result_event(name, content)
    assert payload["tool"] == name
    assert "result" in payload


# ── 15.6 / Property 56 ───────────────────────────────────────────────────────
@given(name=tool_name, content=st.one_of(usable_result_content, error_result_content))
@SETTINGS
def test_property_56_tool_completion_emits_terminal_end(name, content):
    """Feature: deep-quant-analysis-hardening, Property 56: Tool completion emits END with a terminal status"""
    msg = StubToolMessage(content=content, name=name)
    ends = [p for n, p in message_events(msg) if n == TOOL_CALL_END]

    assert len(ends) == 1
    payload = ends[0]
    assert payload["tool"] == name
    assert payload["status"] in ("success", "failure")
    # A failure status always carries a non-empty error reason (R16.5).
    if payload["status"] == "failure":
        assert payload.get("error_reason")


@given(name=tool_name, content=error_result_content)
@SETTINGS
def test_property_56_error_results_report_failure(name, content):
    """Feature: deep-quant-analysis-hardening, Property 56: Tool completion emits END with a terminal status"""
    status, reason = se.tool_result_status(content)
    payload = build_tool_call_end_event(name, status, reason)
    assert payload["status"] == "failure"
    assert payload.get("error_reason")


# ── 15.7 / Property 57 ───────────────────────────────────────────────────────

# A defensibility record using the explicit validator_checks list (VERIFY mode).
verify_record = st.builds(
    lambda checks: {"validator_checks": checks},
    st.lists(
        st.builds(
            lambda c, o: {"check": c, "outcome": o},
            st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=12),
            st.sampled_from(["pass", "fail", "n/a", "informational"]),
        ),
        min_size=1,
        max_size=5,
    ),
)

# A FIND-mode defensibility record (derived four-check protocol).
find_record = st.builds(
    lambda rr, vol, macro: {
        "risk_reward": rr,
        "volatility_basis": vol,
        "macro_trend_conflict": macro,
        "support_resistance": {"s1": 100.0, "r1": 110.0},
        "levels": {"entry": 105.0, "stop_loss": 99.0, "take_profit": 115.0},
    },
    st.floats(allow_nan=False, allow_infinity=False, min_value=0.0, max_value=10.0),
    st.sampled_from(["stop >= 1.5x ATR", "stop < 1.5x ATR", "ATR unavailable", "n/a"]),
    st.sampled_from(["Aligned with 1D trend", "Macro conflict vs 1D", "1D trend unavailable", "n/a"]),
)

defensibility_record = st.one_of(verify_record, find_record)


@given(record=defensibility_record, action=st.sampled_from(["BUY", "SELL", "HOLD"]))
@SETTINGS
def test_property_57_verification_steps_have_check_and_outcome(record, action):
    """Feature: deep-quant-analysis-hardening, Property 57: Verification steps emit VERIFICATION_STEP with check and outcome"""
    decision = {"action": action, "defensibility": record}
    steps = build_verification_steps(decision)

    assert len(steps) >= 1
    for step in steps:
        assert step.get("check")
        assert "outcome" in step

    # Each derived check surfaces as a VERIFICATION_STEP event, in order.
    vsteps = [p for n, p in decision_events(decision) if n == VERIFICATION_STEP]
    assert vsteps == steps


# ── 15.8 / Property 58 ───────────────────────────────────────────────────────
decision_with_rationale = st.builds(
    lambda action, conv, validation: {
        "action": action,
        "conviction_score": conv,
        "setup_validation": validation,
    },
    st.sampled_from(["BUY", "SELL", "HOLD"]),
    st.integers(min_value=0, max_value=100),
    nl_text,
)


@given(decision=decision_with_rationale)
@SETTINGS
def test_property_58_decision_event_shape(decision):
    """Feature: deep-quant-analysis-hardening, Property 58: Finalized decisions emit DECISION with action, conviction, rationale"""
    payload = build_decision_event(decision)
    assert payload is not None
    assert payload["action"] == decision["action"]
    assert payload["conviction_score"] == decision["conviction_score"]
    # Rationale is sourced from setup_validation (or a fallback reason).
    assert payload["rationale"] == decision["setup_validation"]

    events = [(n, p) for n, p in decision_events(decision)]
    decisions = [p for n, p in events if n == DECISION]
    assert len(decisions) == 1
    assert decisions[0] == payload


@given(action=st.sampled_from(["HOLD"]), reason=nl_text)
@SETTINGS
def test_property_58_hold_rationale_falls_back_to_reason(action, reason):
    """Feature: deep-quant-analysis-hardening, Property 58: Finalized decisions emit DECISION with action, conviction, rationale"""
    decision = {"action": action, "conviction_score": 0, "reason": reason}
    payload = build_decision_event(decision)
    assert payload["action"] == action
    assert payload["rationale"] == reason


# ── 15.9 / Property 59 ───────────────────────────────────────────────────────
ZERO_WIDTH = ["\u200b", "\u200c", "\u200d", "\ufeff"]

# A custom-token call block, optionally peppered with zero-width / unicode noise.
markup_block = st.builds(
    lambda name, args, zw: (
        f"{_BEGIN_TOKEN}{_SEP_TOKEN}{name}{zw}"
        + json.dumps(args)
        + f"{_END_TOKEN}"
    ),
    tool_name,
    json_args,
    st.sampled_from(ZERO_WIDTH + [""]),
)

# Stray / orphaned markup tokens that may survive an unterminated block.
orphan_token = st.sampled_from([_BEGIN_TOKEN, _SEP_TOKEN, _END_TOKEN, ""])


@given(
    prefix=st.one_of(nl_text, st.just("")),
    block=markup_block,
    orphan=orphan_token,
    suffix=st.one_of(nl_text, st.just("")),
)
@SETTINGS
def test_property_59_reasoning_has_no_raw_markup(prefix, block, orphan, suffix):
    """Feature: deep-quant-analysis-hardening, Property 59: Reasoning events contain no raw tool-call markup"""
    content = prefix + block + orphan + suffix
    stripped = strip_tool_call_markup(content)

    for token in (_BEGIN_TOKEN, _SEP_TOKEN, _END_TOKEN):
        assert token not in stripped

    # The same guarantee must hold for whatever a REASONING event carries.
    reasoning = build_reasoning_event(content)
    if reasoning is not None:
        for token in (_BEGIN_TOKEN, _SEP_TOKEN, _END_TOKEN):
            assert token not in reasoning["content"]


# ── Run-assembly strategies (15.10-15.14) ────────────────────────────────────

def _ai_update(content="", tool_calls=None):
    return {"messages": [StubAIMessage(content=content, tool_calls=tool_calls or [])]}


def _tool_update(name, content='{"ok": true}'):
    return {"messages": [StubToolMessage(content=content, name=name)]}


# A node update that may contain reasoning, a tool call, a tool result, and/or a
# committed decision.
node_update_strategy = st.one_of(
    st.builds(_ai_update, nl_text, st.just(None)),
    st.builds(lambda n, a: _ai_update("", [_tool_call(n, a)]), tool_name, json_args),
    st.builds(lambda n: _tool_update(n), tool_name),
    st.builds(lambda d: {"decision": d}, decision_with_rationale),
)

node_updates_strategy = st.lists(node_update_strategy, max_size=6)
thread_id_strategy = st.one_of(
    st.text(alphabet=st.characters(min_codepoint=48, max_codepoint=122), min_size=1, max_size=12),
    st.integers(min_value=0, max_value=10_000),
)


# ── 15.10 / Property 60 ──────────────────────────────────────────────────────
@given(
    thread_id=thread_id_strategy,
    updates=node_updates_strategy,
    outcome=st.sampled_from([RUN_COMPLETED, RUN_PAUSED, RUN_ERROR]),
)
@SETTINGS
def test_property_60_run_started_is_first(thread_id, updates, outcome):
    """Feature: deep-quant-analysis-hardening, Property 60: RUN_STARTED is the first event"""
    events = assemble_run_events(thread_id, updates, outcome, error_detail="boom")
    assert events[0][0] == RUN_STARTED
    assert events[0][1]["thread_id"] == thread_id
    # RUN_STARTED appears exactly once and only at the front.
    assert [n for n, _ in events].count(RUN_STARTED) == 1


# ── 15.11 / Property 61 ──────────────────────────────────────────────────────
@given(
    thread_id=thread_id_strategy,
    updates=node_updates_strategy,
    outcome=st.sampled_from([RUN_COMPLETED, RUN_PAUSED]),
)
@SETTINGS
def test_property_61_run_finished_is_final_with_status(thread_id, updates, outcome):
    """Feature: deep-quant-analysis-hardening, Property 61: RUN_FINISHED is the final event with a status"""
    events = assemble_run_events(thread_id, updates, outcome)
    last_name, last_payload = events[-1]
    assert last_name == RUN_FINISHED
    assert last_payload["status"] in (RUN_COMPLETED, RUN_PAUSED)
    assert last_payload["status"] == outcome
    # Exactly one RUN_FINISHED and no ERROR for a completed/paused run.
    assert [n for n, _ in events].count(RUN_FINISHED) == 1
    assert all(n != ERROR for n, _ in events)


# ── 15.12 / Property 62 ──────────────────────────────────────────────────────
@given(
    thread_id=thread_id_strategy,
    names=st.lists(tool_name, min_size=1, max_size=4, unique=True),
    outcome=st.sampled_from([RUN_COMPLETED, RUN_PAUSED]),
)
@SETTINGS
def test_property_62_start_precedes_result_and_end(thread_id, names, outcome):
    """Feature: deep-quant-analysis-hardening, Property 62: A tool call's START precedes its RESULT and END"""
    # For each tool: an AIMessage issuing its call, followed by its ToolMessage.
    updates = []
    for n in names:
        updates.append(_ai_update("", [_tool_call(n, {"timeframe": "15m"})]))
        updates.append(_tool_update(n))

    events = assemble_run_events(thread_id, updates, outcome)

    def first_index(event_name, tool):
        for i, (n, p) in enumerate(events):
            if n == event_name and p.get("tool") == tool:
                return i
        return None

    for n in names:
        start_i = first_index(TOOL_CALL_START, n)
        result_i = first_index(TOOL_CALL_RESULT, n)
        end_i = first_index(TOOL_CALL_END, n)
        assert start_i is not None and result_i is not None and end_i is not None
        assert start_i < result_i < end_i


# ── 15.13 / Property 63 ──────────────────────────────────────────────────────
@given(
    thread_id=thread_id_strategy,
    updates=node_updates_strategy,
    decision=decision_with_rationale,
)
@SETTINGS
def test_property_63_failed_stream_emits_error_no_decision(thread_id, updates, decision):
    """Feature: deep-quant-analysis-hardening, Property 63: A failed LLM stream emits ERROR and no DECISION"""
    # Guarantee at least one committed decision is present in the run updates so
    # the suppression is meaningfully exercised.
    updates = list(updates) + [{"decision": decision}]
    events = assemble_run_events(thread_id, updates, RUN_ERROR, error_detail="stream blew up")

    names = [n for n, _ in events]
    assert ERROR in names
    assert events[-1][0] == ERROR
    assert DECISION not in names
    # No RUN_FINISHED for a failed run.
    assert RUN_FINISHED not in names


# ── 15.14 / Property 64 ──────────────────────────────────────────────────────
@given(
    thread_id=thread_id_strategy,
    updates=node_updates_strategy,
    outcome=st.sampled_from([RUN_COMPLETED, RUN_PAUSED, RUN_ERROR]),
)
@SETTINGS
def test_property_64_every_payload_is_json_object(thread_id, updates, outcome):
    """Feature: deep-quant-analysis-hardening, Property 64: Every stream event payload is a valid JSON object"""
    events = assemble_run_events(thread_id, updates, outcome, error_detail="x")
    for name, payload in events:
        normalized = ensure_json_object(payload)
        assert isinstance(normalized, dict)
        # Round-trips through JSON as an object, and the SSE frame's data line
        # parses back to a JSON object.
        reparsed = json.loads(json.dumps(normalized, default=str))
        assert isinstance(reparsed, dict)

        frame = format_sse(name, payload)
        data_line = next(
            line[len("data: "):] for line in frame.splitlines() if line.startswith("data: ")
        )
        assert isinstance(json.loads(data_line), dict)


# ── 15.15 (example-based unit test) ──────────────────────────────────────────
def test_unit_stream_failure_surfaces_error_and_no_decision():
    """LLM stream-failure end to end: ERROR surfaces, no DECISION/trade plan (R5.5, R17.5)."""
    thread_id = "thread-xyz"
    # A realistic mid-run: reasoning, a market-data tool round-trip, then a
    # committed BUY trade plan — after which the LLM stream fails.
    updates = [
        _ai_update("Establishing macro bias before acting.", None),
        _ai_update("", [_tool_call("get_multi_tf_trend", {"symbol": "RELIANCE"})]),
        _tool_update("get_multi_tf_trend", '{"trend_1h": "Bullish", "trend_4h": "Bullish"}'),
        {
            "decision": {
                "action": "BUY",
                "conviction_score": 74,
                "setup_validation": "Entry at S1 with RR 1:2.4.",
                "execution_plan": "BUY entry 2440, SL 2418, TP 2492",
            }
        },
    ]

    events = assemble_run_events(thread_id, updates, RUN_ERROR, error_detail="connection reset")
    names = [n for n, _ in events]

    # The failed run surfaces a clean ERROR as its final event...
    assert names[0] == RUN_STARTED
    assert names[-1] == ERROR
    error_payload = events[-1][1]
    assert "AI analysis unavailable" in error_payload["error"]
    assert "connection reset" in error_payload["error"]

    # ...and emits NO DECISION / trade plan despite one being present pre-failure.
    assert DECISION not in names
    assert RUN_FINISHED not in names
    for _, payload in events:
        assert "execution_plan" not in payload

    # Genuine reasoning / tool events that occurred before the failure are kept.
    assert REASONING in names
    assert TOOL_CALL_START in names
    assert TOOL_CALL_RESULT in names
    assert TOOL_CALL_END in names
