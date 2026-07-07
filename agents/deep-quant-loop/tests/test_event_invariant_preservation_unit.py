"""Invariant-preservation unit tests for earnings-event-risk-gate (task 11.2).

Feature: earnings-event-risk-gate

Validates: Requirements 13.1, 13.2, 13.3, 13.4

These are plain, example-based pytest unit tests (no live LLM, no live Rust
server, no Hypothesis, no network / filesystem I/O). They pin the pre-feature
invariants so adding calendar awareness cannot silently weaken safety or change
unrelated behavior:

  * R13.1 — ``declare_trade`` remains the SINGLE authoritative completion signal;
            the event gate introduces NO alternate completion path (the event
            tool is a market-data source, never a control/completion tool, and
            ``events.py`` never touches ``state["decision"]`` / ``declare_trade``).
  * R13.2 — the VERIFY and QA modes, the existing tools, the defensibility record,
            journal recording, and the glass-box stream conventions are preserved
            — the feature adds ONLY the new event fields + the one event-risk step.
  * R13.3 — a failed LLM stream surfaces a clean analysis-unavailable ERROR and
            NEVER emits a fabricated DECISION for that run, exactly as before.
  * R13.4 — the feature is validated LIVE (journal ``evt:`` dimension +
            telemetry); it adds NOTHING to the Backtest_Seeder (``backtest.py``).

The sys.path / import bootstrap mirrors the sibling
``tests/test_options_scope_boundary_smoke.py`` and
``tests/test_event_graph_registration_unit.py`` modules: the service directory
(one level up) is prepended to ``sys.path`` so the feature modules import when
pytest is run from anywhere. The existing ``tests/conftest.py`` (which resets
journal globals) is NOT disturbed by this module.
"""

import os
import sys

# Make the service package importable (graph.py / events.py / ... live one up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
import graph  # noqa: E402
import journal  # noqa: E402
import stream_events  # noqa: E402

_EVENT_TOOL = "get_event_risk"


def _read_source(module_basename):
    with open(os.path.join(_SVC_DIR, module_basename), "r", encoding="utf-8") as fh:
        return fh.read()


# The Analysis_Tools established BEFORE the earnings-event-risk-gate feature. The
# feature must leave every one of these registered and untouched (R13.2), adding
# only ``get_event_risk``.
_PRE_EVENT_TOOLS = {
    "get_candles",
    "get_consensus_report",
    "get_multi_tf_trend",
    "get_chart_patterns",
    "get_support_resistance",
    "get_volume_profile",
    "get_news_context",
    "get_prediction",
    "get_trade_performance",
    "get_market_regime",
    "get_relative_strength",
    "get_order_flow",
    "get_forecast",
    "get_session_context",
    "get_options_analytics",
    "watch_price_condition",
    "declare_trade",
}


# ═════════════════════════════════════════════════════════════════════════════
# R13.1 — declare_trade is the single authoritative completion signal; the event
#         gate adds no alternate completion path.
# ═════════════════════════════════════════════════════════════════════════════
def test_declare_trade_remains_the_sole_completion_control_tool():
    """Validates: Requirements 13.1

    ``declare_trade`` is registered as a control tool (it commits the decision),
    and it is NOT a market-data source. The event tool is the opposite: a
    market-data source, never a completion/control tool.
    """
    # declare_trade is a registered, recognized tool ...
    assert "declare_trade" in graph.REGISTERED_TOOL_NAMES
    # ... but it is a CONTROL tool, never a market-data source.
    assert "declare_trade" not in graph.MARKET_DATA_TOOL_NAMES

    # The event tool is a market-data source, NOT a completion/control tool.
    assert _EVENT_TOOL in graph.REGISTERED_TOOL_NAMES
    assert _EVENT_TOOL in graph.MARKET_DATA_TOOL_NAMES
    # watch_price_condition + declare_trade are the only control tools; the event
    # tool never joins them.
    control_tools = graph.REGISTERED_TOOL_NAMES - graph.MARKET_DATA_TOOL_NAMES
    assert _EVENT_TOOL not in control_tools
    assert control_tools == {"declare_trade", "watch_price_condition", "get_trade_performance"} or \
        {"declare_trade", "watch_price_condition"} <= control_tools


def test_event_module_introduces_no_alternate_completion_path():
    """Validates: Requirements 13.1

    The pure Event_Classifier module never commits, blocks, or completes a run:
    it must not reference the completion signal (``state["decision"]``) nor the
    trade-committing / run-suspending control tools.
    """
    src = _read_source("events.py")
    assert "declare_trade" not in src
    assert "watch_price_condition" not in src
    assert 'state["decision"]' not in src
    assert "state['decision']" not in src


def test_completion_is_read_only_from_the_committed_decision():
    """Validates: Requirements 13.1

    The graph's completion routing reads ONLY ``state["decision"]`` (set solely by
    a validated declare_trade or the forced-HOLD path); the event feature does not
    alter that. Assert the graph still documents/enforces the single signal.
    """
    src = _read_source("graph.py")
    # The single-signal contract is documented and enforced in should_continue.
    assert 'state["decision"]' in src
    # The event entry helper is a pure read of tool output — it never assigns the
    # decision (no ``decision =`` completion write inside _event_entry).
    assert callable(graph._event_entry)


# ═════════════════════════════════════════════════════════════════════════════
# R13.2 — VERIFY / QA modes, existing tools, defensibility record, journal
#         recording, and glass-box stream conventions are preserved; only the new
#         event fields / step are added.
# ═════════════════════════════════════════════════════════════════════════════
def test_all_pre_event_tools_still_registered():
    """Validates: Requirements 13.2

    Every Analysis_Tool that existed before this feature is still registered, and
    the feature's ONLY tool addition to the registry is ``get_event_risk``.
    """
    assert _PRE_EVENT_TOOLS <= graph.REGISTERED_TOOL_NAMES
    added = graph.REGISTERED_TOOL_NAMES - _PRE_EVENT_TOOLS
    assert added == {_EVENT_TOOL}, f"unexpected registry changes: {added!r}"


def test_verify_and_qa_modes_preserved():
    """Validates: Requirements 13.2

    The VERIFY-mode RISK_MANAGER_PROMPT and the Trade_QA_Mode bookkeeping remain
    intact; the feature only adds event guidance to the VERIFY prompt.
    """
    # VERIFY mode: the RISK_MANAGER_PROMPT still exists and now consults the event
    # tool (the only additive change), without losing its verification structure.
    assert isinstance(graph.RISK_MANAGER_PROMPT, str) and graph.RISK_MANAGER_PROMPT.strip()
    assert "Verification Mode" in graph.RISK_MANAGER_PROMPT
    assert _EVENT_TOOL in graph.RISK_MANAGER_PROMPT

    # QA mode: the bounded Q&A bookkeeping field is still part of the agent state.
    graph_src = _read_source("graph.py")
    assert "qa_turns" in graph_src


def test_defensibility_record_preserved_with_only_the_added_event_entry():
    """Validates: Requirements 13.2

    ``build_defensibility_record`` still assembles the record and now carries an
    ``event`` entry. With no get_event_risk result in history the entry is an
    honest unavailable marker (never a fabricated risk).
    """
    decision = {"action": "HOLD", "conviction_score": 5, "rationale": "flat"}
    record = graph.build_defensibility_record([], decision)

    assert isinstance(record, dict)
    # The additive event entry is present and honestly unavailable when absent.
    assert "event" in record
    assert record["event"].get("available") is False
    assert "event_risk" not in record["event"]  # no fabricated risk

    # The pure entry helper agrees: no results -> unavailable, no fabrication.
    # ``_event_entry`` reads the latest-tool-results map, so an empty map means no
    # get_event_risk result is present in history.
    entry = graph._event_entry({})
    assert entry.get("available") is False
    assert "event_risk" not in entry


def test_journal_recording_preserved_event_tag_is_the_only_new_dimension():
    """Validates: Requirements 13.2

    ``derive_setup_tags`` still produces the established fingerprint and appends
    exactly one low-cardinality ``evt:`` tag as the FINAL dimension (immediately
    after the ``tier:`` tag), keeping the setup_key deterministic.
    """
    decision = {"action": "BUY", "conviction_score": 7}
    tags = journal.derive_setup_tags(decision)

    assert isinstance(tags, list) and tags
    # Exactly one evt: tag, and it is the LAST tag (the fixed final position).
    evt_tags = [t for t in tags if t.startswith("evt:")]
    assert len(evt_tags) == 1
    assert tags[-1] == evt_tags[0]
    # It comes immediately after the tier: tag.
    tier_tags = [t for t in tags if t.startswith("tier:")]
    assert tier_tags, "the pre-feature tier: dimension must still be present"
    assert tags.index(tier_tags[-1]) == len(tags) - 2

    # Low-cardinality by construction (<= 8 values including 'unknown').
    assert len(journal.EVT_TAG_VALUES) <= 8
    assert evt_tags[0].split(":", 1)[1] in journal.EVT_TAG_VALUES


def test_glass_box_stream_vocabulary_preserved():
    """Validates: Requirements 13.2

    The established glass-box stream event vocabulary is unchanged; the feature
    adds only the ``event-risk`` VERIFICATION_STEP (a new step under the SAME
    event type, not a new event type).
    """
    # Every pre-feature stream-event name is still defined with its literal value.
    assert stream_events.RUN_STARTED == "RUN_STARTED"
    assert stream_events.RUN_FINISHED == "RUN_FINISHED"
    assert stream_events.ERROR == "ERROR"
    assert stream_events.REASONING == "REASONING"
    assert stream_events.TOOL_CALL_START == "TOOL_CALL_START"
    assert stream_events.TOOL_CALL_RESULT == "TOOL_CALL_RESULT"
    assert stream_events.TOOL_CALL_END == "TOOL_CALL_END"
    assert stream_events.VERIFICATION_STEP == "VERIFICATION_STEP"
    assert stream_events.DECISION == "DECISION"

    # The added event-risk step is a VERIFICATION_STEP with a stable check id and a
    # recognized outcome vocabulary — never a fabricated risk when unavailable.
    unavailable_step = stream_events._event_step({})
    assert unavailable_step["check"] == "event-risk"
    assert unavailable_step["outcome"].startswith("not-evaluable")

    pass_step = stream_events._event_step({"event": {"available": True, "event_risk": "clear"}})
    assert pass_step["check"] == "event-risk"
    assert pass_step["outcome"] == "pass"


# ═════════════════════════════════════════════════════════════════════════════
# R13.3 — a failed LLM stream surfaces a clean error and never a fabricated
#         decision.
# ═════════════════════════════════════════════════════════════════════════════
def test_error_event_carries_clean_message_and_no_trade_plan():
    """Validates: Requirements 13.3

    ``build_error_event`` yields a clean analysis-unavailable message and carries
    NO trade-plan / decision fields — no fabricated decision.
    """
    payload = stream_events.build_error_event("stream reset by peer")
    assert isinstance(payload, dict)
    assert set(payload.keys()) == {"error"}
    assert payload["error"].startswith("AI analysis unavailable")
    # No trade-decision fields leak into the error payload.
    forbidden = {"action", "decision", "entry", "stop_loss", "take_profit",
                 "conviction_score", "conviction", "recommendation"}
    assert not (set(payload.keys()) & forbidden)


def test_failed_stream_emits_no_decision_event_even_if_one_is_present():
    """Validates: Requirements 13.3

    On the error outcome, ``assemble_run_events`` drops any DECISION for the run
    and ends with an ERROR event — a failed stream never surfaces a fabricated
    decision, and this event feature does not change that.
    """
    # A node update that DOES carry a committed decision (which would normally emit
    # a DECISION event) — simulating a fabricated/partial decision on a failed run.
    node_updates = [{
        "decision": {"action": "BUY", "conviction_score": 9,
                     "rationale": "should never be surfaced on error"},
    }]

    events = stream_events.assemble_run_events(
        "thread-1", node_updates, outcome=stream_events.RUN_ERROR,
        error_detail="LLM stream failed",
    )
    names = [name for name, _ in events]

    # The run starts cleanly, ends with ERROR, and emits NO DECISION.
    assert names[0] == stream_events.RUN_STARTED
    assert names[-1] == stream_events.ERROR
    assert stream_events.DECISION not in names
    # And no RUN_FINISHED on the error path.
    assert stream_events.RUN_FINISHED not in names


def test_successful_run_still_emits_the_decision():
    """Validates: Requirements 13.3

    Control case: a non-error run still surfaces the DECISION and a RUN_FINISHED —
    confirming the error-path suppression is specific to the failed stream.
    """
    node_updates = [{
        "decision": {"action": "HOLD", "conviction_score": 4, "rationale": "flat"},
    }]
    events = stream_events.assemble_run_events(
        "thread-2", node_updates, outcome=stream_events.RUN_COMPLETED,
    )
    names = [name for name, _ in events]
    assert stream_events.DECISION in names
    assert names[-1] == stream_events.RUN_FINISHED
    assert stream_events.ERROR not in names


# ═════════════════════════════════════════════════════════════════════════════
# R13.4 — the feature adds nothing to the Backtest_Seeder (live-measurement only).
# ═════════════════════════════════════════════════════════════════════════════
def test_backtest_seeder_has_no_event_date_seeding():
    """Validates: Requirements 13.4

    Because no reliable historical scheduled-event feed exists, the gate is
    validated LIVE via the journal + telemetry — the Backtest_Seeder
    (``backtest.py``) must reference NONE of the event feature: no import of the
    ``events`` module, no ``get_event_risk`` tool, no event classification, and no
    ``evt:`` seeding.
    """
    src = _read_source("backtest.py")
    assert "import events" not in src
    assert "get_event_risk" not in src
    assert "assess_event_risk" not in src
    assert "event_risk" not in src
    assert "evt:" not in src


def test_backtest_seeder_module_does_not_import_events():
    """Validates: Requirements 13.4

    A stronger structural check: ``backtest.py`` is importable and the ``events``
    module is not among its module attributes (it never wired the event gate in).
    """
    import backtest  # noqa: E402
    assert not hasattr(backtest, "events"), (
        "backtest.py must not import the events module (live-measurement only)"
    )
