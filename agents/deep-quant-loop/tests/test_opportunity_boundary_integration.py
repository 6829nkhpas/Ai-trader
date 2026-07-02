"""Unit tests for the Adaptive Opportunity Engine boundary integrations (task 13.5).

Feature: adaptive-opportunity-engine

Covers the four boundary modules the engine tags:
  - ``journal.derive_setup_tags`` appends a fixed-position, low-cardinality
    ``tier:`` dimension last (after ``opt:``) — R9.2.
  - ``telemetry.finalize_session`` records the committed ``opportunity_tier`` and the
    bounded-hunt termination reason / heartbeat usage on the ``SessionRecord`` — R9.3.
  - ``stream_events.build_best_current_read_event`` frames the non-committal read
    through the standard conventions and never carries a committed trade — R8.4.
  - ``build_decision_event`` carries the tier for the UI / telemetry tee.
"""

import os
import sys

# Make the service package importable (modules live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
import telemetry  # noqa: E402
import stream_events  # noqa: E402
import opportunity  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# journal.derive_setup_tags: the tier: dimension is appended last, low-cardinality
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R9.2: derive_setup_tags appends a fixed-position low-cardinality tier: dimension last.
def test_tier_tag_is_last_and_low_cardinality():
    """Validates: Requirements 9.2"""
    for tier in ("a_plus", "b_continuation", "scalp", "stand_aside", "garbage", None):
        decision = {"action": "BUY", "opportunity_tier": tier}
        tags = journal.derive_setup_tags(decision)
        # The tier dimension is the FINAL tag, after the options ``opt:`` tag.
        assert tags[-1].startswith("tier:"), tags
        opt_idx = max(i for i, t in enumerate(tags) if t.startswith("opt:"))
        tier_idx = len(tags) - 1
        assert tier_idx > opt_idx  # tier: comes after opt:
        # Low cardinality: the value is one of at most five.
        assert tags[-1] in ["tier:" + v for v in opportunity.TIER_TAG_VALUES]

    # A missing tier collapses to tier:unknown deterministically.
    assert journal.derive_setup_tags({"action": "HOLD"})[-1] == "tier:unknown"


# ─────────────────────────────────────────────────────────────────────────────
# telemetry: the committed tier + termination reason land on the SessionRecord
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R9.3: finalize_session records opportunity_tier + termination reason on the SessionRecord.
def test_finalize_session_records_opportunity_fields():
    """Validates: Requirements 9.3"""
    state = telemetry.SessionState(thread_id="t1", started_at=100.0, ended_at=110.0)
    state.opportunity_tier = "b_continuation"
    state.opportunity_termination_reason = "watch-cap-reached"

    record = telemetry.finalize_session(state)
    assert record.opportunity_tier == "b_continuation"
    assert isinstance(record.extra, dict)
    assert record.extra.get("termination_reason") == "watch-cap-reached"


# Feature: adaptive-opportunity-engine, R9.3/R3.4: a run without the engine records NULL opportunity fields (never fabricated).
def test_finalize_session_defaults_none_without_engine():
    """Validates: Requirements 9.3, 3.4"""
    state = telemetry.SessionState(thread_id="t2", started_at=100.0, ended_at=110.0)
    record = telemetry.finalize_session(state)
    assert record.opportunity_tier is None
    assert record.extra is None  # no empty object fabricated


# ─────────────────────────────────────────────────────────────────────────────
# stream_events: the Best_Current_Read is framed non-committally
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R8.4: build_best_current_read_event frames only the assessment fields, never a committed trade.
def test_best_current_read_event_is_non_committal():
    """Validates: Requirements 8.1, 8.3, 8.4"""
    read = opportunity.best_current_read(
        {"entry": 100.0, "target": 110.0, "stop": 95.0,
         "regime": {"available": True, "favorability": "favorable"}},
        None,
    )
    event = stream_events.build_best_current_read_event(read)
    assert set(event.keys()) == {"bias", "levels", "why_standing_aside"}
    for forbidden in ("action", "conviction_score", "execution_plan"):
        assert forbidden not in event
    # A non-dict read yields None (nothing surfaced).
    assert stream_events.build_best_current_read_event(None) is None
    # The event name constant exists.
    assert stream_events.BEST_CURRENT_READ == "BEST_CURRENT_READ"


# Feature: adaptive-opportunity-engine, R8.1: a stand_aside decision surfaces a BEST_CURRENT_READ before the DECISION.
def test_decision_events_emits_best_current_read_on_stand_aside():
    """Validates: Requirements 8.1, 8.4"""
    decision = {
        "action": "HOLD",
        "conviction_score": 0,
        "opportunity_tier": "stand_aside",
        "setup_validation": "standing aside",
        "execution_plan": "HOLD",
        "best_current_read": {"bias": "neutral", "levels": {}, "why_standing_aside": "no setup"},
    }
    names = [name for name, _payload in stream_events.decision_events(decision)]
    assert stream_events.BEST_CURRENT_READ in names
    # The read is ordered before the DECISION event.
    assert names.index(stream_events.BEST_CURRENT_READ) < names.index(stream_events.DECISION)


# Feature: adaptive-opportunity-engine, R9.2: the DECISION event carries the committed tier for the UI / telemetry tee.
def test_decision_event_carries_tier():
    """Validates: Requirements 9.2, 9.3"""
    event = stream_events.build_decision_event(
        {"action": "BUY", "conviction_score": 70, "opportunity_tier": "scalp", "size_factor": 0.3}
    )
    assert event["opportunity_tier"] == "scalp"
    assert event["size_factor"] == 0.3
    # A decision without a tier omits the key (no fabrication).
    plain = stream_events.build_decision_event({"action": "HOLD", "conviction_score": 0})
    assert "opportunity_tier" not in plain
