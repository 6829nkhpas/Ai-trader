"""Property-based test for the journal event-risk setup tag (journal.py, task 7.2).

Feature: earnings-event-risk-gate

This module implements design **Property 24: Exactly one low-cardinality event
tag at a fixed position**:

    For any decision, ``derive_setup_tags`` appends exactly one ``evt:<value>``
    tag at a fixed position in the tag sequence, where ``<value>`` is drawn from
    the fixed enumeration of at most 8 values (including ``unknown``); a decision
    lacking a valid recorded event entry yields ``evt:unknown``; and identical
    decisions yield an identical ``setup_key``.

Validates: Requirements 10.1, 10.2, 10.3.

The strategy generates committed decisions whose defensibility ``event`` entry
spans a wide space of shapes:

  * a usable Event_Assessment carrying every categorical Event_Risk value
    (``clear``/``imminent``/``through_event``) crossed with assorted
    ``days_until_event`` / ``event_date`` / ``event_recommendation`` / ``available``
    shapes;
  * an explicitly unavailable entry (``available: False``);
  * a malformed entry (non-dict event, missing ``event_risk``, unrecognized /
    empty Event_Risk value);
  * an absent entry (no ``event`` key, no ``defensibility`` key at all).

For each generated decision it asserts (against an independent reference that
re-derives the design's collapsing rule):

  * exactly one tag in ``derive_setup_tags`` begins with ``evt:``;
  * its value is one of the fixed ``EVT_TAG_VALUES`` (at most 8 distinct,
    including ``unknown``);
  * the tag sits at a FIXED position — the FINAL slot of the tag sequence,
    immediately after the opportunity ``tier:`` tag;
  * the collapsing rule holds: a recorded ``clear``/``imminent``/``through_event``
    Event_Risk is carried verbatim; missing/unavailable/unrecognized -> ``unknown``;
  * ``setup_key_from_tags`` is deterministic — identical decisions yield an
    identical ``setup_key`` across repeated derivations.

The sys.path / import pattern mirrors the sibling ``test_*_setup_tag_properties``
modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (journal.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from journal import (  # noqa: E402
    EVT_TAG_VALUES,
    derive_setup_tags,
    setup_key_from_tags,
)

# The three categorical Event_Risk values a usable Event_Assessment can carry.
_EVENT_RISK_VALUES = {"clear", "imminent", "through_event"}


def _expected_event_value(decision: dict) -> str:
    """Independently re-derive the design's Event_Risk collapse.

    Mirrors the design rule: a recorded ``clear``/``imminent``/``through_event``
    Event_Risk is carried verbatim; anything missing / unavailable / empty /
    unrecognized -> ``unknown``.
    """
    d = decision or {}
    deff = d.get("defensibility") or {}
    event = deff.get("event")
    if not isinstance(event, dict):
        return "unknown"
    if event.get("available") is False:
        return "unknown"
    event_risk = str(event.get("event_risk") or "").strip().lower()
    return event_risk if event_risk in _EVENT_RISK_VALUES else "unknown"


@st.composite
def _event_entry(draw):
    """A defensibility ``event`` entry spanning usable / unavailable / malformed."""
    kind = draw(
        st.sampled_from(
            [
                "risk",           # usable Event_Assessment
                "risk_avail",     # usable assessment with explicit available: True
                "unavailable",    # explicit available: False
                "unknown_risk",   # unrecognized / empty event_risk string
                "missing_risk",   # no event_risk key
                "non_dict",       # event is not a dict
            ]
        )
    )
    if kind in ("risk", "risk_avail"):
        entry = {
            "event_risk": draw(st.sampled_from(sorted(_EVENT_RISK_VALUES))),
            "days_until_event": draw(
                st.one_of(st.none(), st.integers(min_value=0, max_value=30))
            ),
            "event_date": draw(
                st.sampled_from(["2024-01-25", "2024-06-30", "", None])
            ),
            "event_recommendation": draw(
                st.sampled_from(
                    ["proceed", "reduce_exposure", "avoid_or_stand_aside", None]
                )
            ),
        }
        if kind == "risk_avail":
            entry["available"] = True
        # carry assorted extra fields that must be ignored by the tag derivation
        entry["symbol"] = draw(st.sampled_from([None, "RELIANCE", "NIFTY"]))
        entry["holding_horizon"] = draw(
            st.sampled_from([None, "intraday", "multi_session"])
        )
        return entry
    if kind == "unavailable":
        return {"available": False, "reason": "no usable get_event_risk assessment"}
    if kind == "unknown_risk":
        return {
            "event_risk": draw(
                st.sampled_from(["", "none", "CLEAR ", "earnings", "high", "xyz"])
            ),
            "days_until_event": draw(st.one_of(st.none(), st.integers(0, 30))),
        }
    if kind == "missing_risk":
        return {"days_until_event": draw(st.one_of(st.none(), st.integers(0, 30)))}
    # non_dict
    return draw(st.sampled_from([None, "clear", 7, ["imminent"], True]))


@st.composite
def _decision(draw):
    """A committed decision with an assorted defensibility/event shape."""
    shape = draw(
        st.sampled_from(
            [
                "with_event",   # defensibility carries an event entry
                "no_event",     # defensibility present but no event key
                "no_deff",      # no defensibility key at all
            ]
        )
    )
    action = draw(st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", None]))
    decision = {}
    if action is not None:
        decision["action"] = action
    if shape == "with_event":
        decision["defensibility"] = {"event": draw(_event_entry())}
    elif shape == "no_event":
        decision["defensibility"] = {"regime": {"available": False}}
    # shape == "no_deff": leave defensibility absent entirely
    return decision


# ─────────────────────────────────────────────────────────────────────────────
# Property 24: Exactly one low-cardinality event tag at a fixed position
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 24: Exactly one low-cardinality event tag at a fixed position
@settings(max_examples=25, deadline=None)
@given(decision=_decision())
def test_property_24_event_tag_low_cardinality_fixed_position(decision):
    """Validates: Requirements 10.1, 10.2, 10.3

    ``derive_setup_tags`` appends exactly one ``evt:<value>`` tag, drawn from the
    fixed at-most-8-value enumeration, at a fixed (final) position immediately
    after the ``tier:`` tag; a decision lacking a valid event entry yields
    ``evt:unknown``; and identical decisions yield an identical ``setup_key``.
    """
    # Static guard: the enumeration is low-cardinality (at most 8 incl. unknown).
    assert len(EVT_TAG_VALUES) <= 8, f"EVT_TAG_VALUES too large: {EVT_TAG_VALUES}"
    assert "unknown" in EVT_TAG_VALUES

    tags = derive_setup_tags(decision)

    # ── Exactly one event tag (R10.1). ───────────────────────────────────────
    evt_tags = [t for t in tags if t.startswith("evt:")]
    assert len(evt_tags) == 1, f"expected exactly one evt: tag, got {evt_tags}"
    evt_tag = evt_tags[0]
    value = evt_tag.split(":", 1)[1]

    # ── Value drawn from the fixed low-cardinality enumeration (R10.3). ───────
    assert value in EVT_TAG_VALUES, f"evt value {value!r} not in {EVT_TAG_VALUES}"

    # ── Fixed position: the event tag is the FINAL tag, immediately after the
    #    opportunity ``tier:`` tag. ───────────────────────────────────────────
    evt_index = tags.index(evt_tag)
    assert evt_index == len(tags) - 1, (
        f"evt tag not at the final position in {tags}"
    )
    assert tags[evt_index - 1].startswith("tier:"), (
        f"evt tag not immediately after tier: tag in {tags}"
    )

    # ── Collapsing rule holds against the independent reference (R10.2/R10.3). ─
    expected = _expected_event_value(decision)
    assert value == expected, (
        f"evt value {value!r} != expected {expected!r} for decision {decision!r}"
    )

    # ── A decision lacking a valid event entry yields evt:unknown (R10.2). ────
    deff = decision.get("defensibility") or {}
    event = deff.get("event")
    has_valid_entry = (
        isinstance(event, dict)
        and event.get("available") is not False
        and str(event.get("event_risk") or "").strip().lower() in _EVENT_RISK_VALUES
    )
    if not has_valid_entry:
        assert value == "unknown", (
            f"expected evt:unknown for invalid/absent entry, got {value!r}"
        )

    # ── setup_key determinism: identical input -> identical setup_key (R10.1). ─
    tags_again = derive_setup_tags(decision)
    assert setup_key_from_tags(tags) == setup_key_from_tags(tags_again)
    # A deep-equal copy of the decision yields the identical setup_key too.
    import copy

    key_copy = setup_key_from_tags(derive_setup_tags(copy.deepcopy(decision)))
    assert setup_key_from_tags(tags) == key_copy
