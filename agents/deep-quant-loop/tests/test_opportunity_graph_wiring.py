"""Unit tests for the Adaptive Opportunity Engine graph wiring (graph.py, task 9.4).

Feature: adaptive-opportunity-engine

These tests exercise the loop-control integration of the engine into ``graph.py``:
  - ``should_continue`` — the Watch_Cap / Session_Budget gate that routes a pending
    watch to ``force_terminal`` instead of ``suspend`` when the bounded hunt is
    exhausted (R3.1-3.3, 3.5), and preserves the ``suspend`` route otherwise.
  - ``force_terminal`` — commits a terminal stand-aside decision that answers the
    pending watch call, cites the termination reason, and carries the tier +
    Best_Current_Read (R3, R8).
  - ``tool_node`` — the invalidation-resume detection that arms the post-mortem
    (R4.1, R4.4) and the re-arm gate that suppresses an unchanged re-arm (R4.2).
  - ``_finalize_decision`` — stamps the Opportunity_Tier on every committed
    decision (R1.5), including a committed ``declare_trade``.

The real LLM and network are never invoked; lightweight stub messages stand in and
``graph._base_tool_node`` is patched where a code path would dispatch to a live
tool. ``journal.record_decision`` is patched to keep the tests hermetic.
"""

import json
import os
import sys
from unittest import mock

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
import opportunity  # noqa: E402


# ── Lightweight stub messages (mirror tests/test_loop_routing.py) ─────────────
class StubAIMessage:
    def __init__(self, content="", tool_calls=None, extraction_status=None, synthetic=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        self.additional_kwargs = {
            "_extraction_status": extraction_status or {},
            "_synthetic_results": synthetic or {},
        }


class StubToolMessage:
    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _watch_ai(args, call_id="w1"):
    """An assistant message carrying a single ok watch_price_condition call."""
    return StubAIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "watch_price_condition", "args": args}],
        extraction_status={call_id: "ok"},
    )


class FakeToolNode:
    """Returns one ToolMessage per pending call (no network / interrupt)."""

    def __init__(self, content_by_name=None):
        self._by_name = content_by_name or {}

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = getattr(msgs[0], "tool_calls", None) or [] if msgs else []
        return {
            "messages": [
                StubToolMessage(content=self._by_name.get(c.get("name"), "ok"), name=c.get("name"))
                for c in calls
            ]
        }


_THESIS = {"symbol": "RELIANCE", "timeframe": "15m", "price_level": 2500.0, "direction": "above"}


# ─────────────────────────────────────────────────────────────────────────────
# should_continue: Watch_Cap / Session_Budget gate ahead of the suspend route
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R3: a pending watch below the bounds suspends; at/over the Watch_Cap it routes to force_terminal.
def test_watch_below_cap_suspends_at_cap_force_terminal():
    """Validates: Requirements 3.1, 3.3, 3.5"""
    watch_msg = _watch_ai(_THESIS)

    # Below the Watch_Cap (default 3): normal suspend.
    below = {"messages": [watch_msg], "decision": None, "reasoning_turns": 0,
             "market_data_seen": True, "watch_cycles": 0}
    assert graph.should_continue(below) == "suspend"

    # At/over the Watch_Cap: the bounded hunt is closed -> force_terminal.
    at_cap = dict(below)
    at_cap["watch_cycles"] = graph._OPPORTUNITY_CFG.watch_cap
    assert graph.should_continue(at_cap) == "force_terminal"


# Feature: adaptive-opportunity-engine, R3.2: a session-budget exhaustion with no pending work routes to force_terminal.
def test_session_budget_exhausted_forces_terminal():
    """Validates: Requirements 3.2, 3.3"""
    reasoning_only = StubAIMessage(content="still thinking", tool_calls=[])
    state = {
        "messages": [reasoning_only],
        "decision": None,
        "reasoning_turns": 0,
        "market_data_seen": True,
        "session_turns": graph._OPPORTUNITY_CFG.session_max_turns,  # budget spent
    }
    assert graph.should_continue(state) == "force_terminal"


# ─────────────────────────────────────────────────────────────────────────────
# force_terminal: commits a terminal stand-aside decision, answers the watch call
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R3/R8: force_terminal commits a stand_aside HOLD citing the termination reason and answers the pending watch call.
def test_force_terminal_commits_stand_aside_and_answers_watch():
    """Validates: Requirements 3.3, 1.5, 8.1, 10.1"""
    watch_msg = _watch_ai(_THESIS)
    state = {
        "messages": [watch_msg],
        "decision": None,
        "market_data_seen": True,
        "watch_cycles": graph._OPPORTUNITY_CFG.watch_cap,  # watch-cap-reached
        "session_turns": 1,
    }
    with mock.patch.object(graph.journal, "record_decision", return_value=None):
        update = graph.force_terminal(state)

    decision = update["decision"]
    assert decision["action"] == "HOLD"
    assert decision["reason"] == "watch-cap-reached"
    assert decision["source"] == "force_terminal"
    # A committed terminal decision carries its tier (stand_aside) and a read.
    assert decision["opportunity_tier"] == "stand_aside"
    assert isinstance(decision.get("best_current_read"), dict)
    assert set(decision["best_current_read"]) == {"bias", "levels", "why_standing_aside"}
    # The pending watch call is answered with a ToolMessage (no orphaned call).
    watch_answers = [
        m for m in update["messages"]
        if getattr(m, "name", None) == "watch_price_condition"
    ]
    assert len(watch_answers) == 1


# ─────────────────────────────────────────────────────────────────────────────
# tool_node: invalidation resume arms the post-mortem and counts the Watch_Cycle
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R4.1/R4.4: an invalidation resume sets postmortem_pending, captures the prior thesis, and counts toward the Watch_Cap.
def test_invalidation_resume_arms_postmortem():
    """Validates: Requirements 4.1, 4.4"""
    watch_msg = _watch_ai(_THESIS)
    state = {
        "messages": [watch_msg],
        "decision": None,
        "market_data_seen": True,
        "watch_cycles": 1,
        "invalidation_count": 0,
    }
    fake = FakeToolNode(
        {"watch_price_condition": "Setup INVALIDATED: price moved against the setup. candle=..."}
    )
    with mock.patch.object(graph, "_base_tool_node", fake):
        update = graph.tool_node(state)

    assert update.get("postmortem_pending") is True
    assert update.get("invalidation_count") == 1
    assert update.get("watch_cycles") == 2  # invalidation counts toward the cap (R4.4)
    assert update.get("last_resume_kind") == opportunity.RESUME_INVALIDATION
    # The captured prior thesis fingerprints the just-invalidated watch.
    assert update["prior_thesis"] == opportunity.thesis_fingerprint(_THESIS)


# ─────────────────────────────────────────────────────────────────────────────
# tool_node: re-arm gate suppresses an unchanged re-arm, allows a changed one
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R4.2: while a post-mortem is pending, an unchanged re-arm is suppressed (not registered) and answered with feedback.
def test_unchanged_rearm_suppressed_during_postmortem():
    """Validates: Requirements 4.2"""
    # Provide an ATR so the volatility tolerance can be derived.
    consensus = StubToolMessage(content=json.dumps({"atr_14": 10.0, "current_price": 2490.0}),
                                name="get_consensus_report")
    rearm = _watch_ai(dict(_THESIS))  # identical thesis
    state = {
        "messages": [consensus, rearm],
        "decision": None,
        "market_data_seen": True,
        "postmortem_pending": True,
        "prior_thesis": opportunity.thesis_fingerprint(_THESIS),
    }
    # If the watch were NOT suppressed it would dispatch to the real tool (interrupt);
    # patching guards against that and lets us assert it was never executed.
    executed = {"called": False}

    class Guard:
        def invoke(self, payload):
            executed["called"] = True
            return {"messages": []}

    with mock.patch.object(graph, "_base_tool_node", Guard()):
        with mock.patch.object(graph.journal, "record_decision", return_value=None):
            update = graph.tool_node(state)

    # The unchanged re-arm was suppressed: the tool was never executed, and the
    # call was answered with feedback rather than registering a watch.
    assert executed["called"] is False
    assert update.get("decision") is None
    feedback = [m for m in update["messages"] if getattr(m, "name", None) == "watch_price_condition"]
    assert len(feedback) == 1
    assert "suppressed" in feedback[0].content.lower()


# Feature: adaptive-opportunity-engine, R4.2/R4.3: a changed re-arm passes the gate and is executed.
def test_changed_rearm_allowed_during_postmortem():
    """Validates: Requirements 4.2, 4.3"""
    consensus = StubToolMessage(content=json.dumps({"atr_14": 10.0, "current_price": 2600.0}),
                                name="get_consensus_report")
    # A materially different thesis (different direction + level).
    changed = _watch_ai({"symbol": "RELIANCE", "timeframe": "15m",
                         "price_level": 2400.0, "direction": "below",
                         "invalidation_level": 2405.0})
    state = {
        "messages": [consensus, changed],
        "decision": None,
        "market_data_seen": True,
        "postmortem_pending": True,
        "prior_thesis": opportunity.thesis_fingerprint(_THESIS),
    }
    executed = {"called": False}

    class Guard:
        def invoke(self, payload):
            executed["called"] = True
            calls = getattr(payload["messages"][0], "tool_calls", None) or []
            return {"messages": [StubToolMessage(content="watching_registered", name=c["name"]) for c in calls]}

    with mock.patch.object(graph, "_base_tool_node", Guard()):
        update = graph.tool_node(state)

    # The changed re-arm passed the gate and was executed (registered).
    assert executed["called"] is True


# ─────────────────────────────────────────────────────────────────────────────
# _finalize_decision: stamps the Opportunity_Tier on a committed declare_trade
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R1.5: a committed directional decision carries an evidence-derived tier and size factor.
def test_committed_declare_is_tier_tagged():
    """Validates: Requirements 1.5, 10.2"""
    # Evidence: a defensible long triple + a strong pattern + several aligned signals.
    patterns = StubToolMessage(
        content=json.dumps([{"pattern_type": "Inverse H&S", "confidence": 0.8, "sentiment": "Bullish"}]),
        name="get_chart_patterns",
    )
    regime = StubToolMessage(
        content=json.dumps({"trend_state": "trending", "volatility_state": "normal", "favorability": "favorable"}),
        name="get_market_regime",
    )
    decision = {
        "action": "BUY",
        "conviction_score": 80,
        "setup_validation": "long setup",
        "execution_plan": "buy",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 115.0,
        "source": "declare_trade",
    }
    state = {"messages": [patterns, regime], "mode": "FIND", "manual_trade": None,
             "symbol": "RELIANCE", "timeframe": "15m"}
    with mock.patch.object(graph.journal, "record_decision", return_value=None):
        graph._finalize_decision(state, decision)

    # The committed directional decision is tier-tagged (a tradeable tier) with a
    # size factor in (0, 1], and the validated levels are untouched (R10.2).
    assert decision["opportunity_tier"] in ("a_plus", "b_continuation", "scalp")
    assert 0.0 < decision["size_factor"] <= 1.0
    assert decision["entry"] == 100.0 and decision["stop_loss"] == 95.0 and decision["take_profit"] == 115.0
    # The tier is mirrored into the defensibility record (R9.1).
    assert decision["defensibility"].get("opportunity_tier") == decision["opportunity_tier"]
