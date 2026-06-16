"""Property-based test for the journal session setup tag (journal.py, task 7.2).

Feature: session-expiry-awareness

This module implements design **Property 22: Exactly one low-cardinality session
tag at a fixed position**:

    For any decision, ``derive_setup_tags`` appends exactly one ``sess:<value>``
    tag at a fixed position in the tag sequence, where ``<value>`` is drawn from
    the fixed enumeration of at most 8 values (including ``unknown``); a decision
    lacking a valid recorded session entry yields ``sess:unknown``; and identical
    decisions yield an identical ``setup_key``.

Validates: Requirements 10.1, 10.2, 10.3.

The strategy generates committed decisions whose defensibility ``session`` entry
spans a wide space of states:

  * a usable Session_Label in every Session_Phase (the seven phases) crossed with
    both expiry-day flags (and assorted ``days_until_expiry`` / ``available``
    shapes);
  * an explicitly unavailable entry (``available: False``);
  * a malformed entry (non-dict session, missing ``session_phase``, unrecognized
    phase, non-dict ``expiry_context``);
  * an absent entry (no ``session`` key, no ``defensibility`` key at all).

For each generated decision it asserts (against an independent reference that
re-derives the design's collapsing rules):

  * exactly one tag in ``derive_setup_tags`` begins with ``sess:``;
  * its value is one of the fixed ``SESS_TAG_VALUES`` (at most 8 distinct,
    including ``unknown``);
  * the tag sits at a FIXED position — the last slot of the tag sequence;
  * the collapsing rules hold: expiry-day ``afternoon``/``closing`` -> ``expiry``;
    ``pre_open``/``post_close`` -> ``offhours``; each remaining in-session phase ->
    its own bucket; missing/unavailable/unrecognized -> ``unknown``;
  * ``setup_key_from_tags`` is deterministic — identical decisions yield an
    identical ``setup_key`` across repeated derivations.

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules.
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
    SESS_TAG_VALUES,
    derive_setup_tags,
    setup_key_from_tags,
)

# The seven Session_Phase values the classifier can emit.
_ALL_PHASES = [
    "pre_open", "opening", "morning", "midday", "afternoon", "closing", "post_close",
]
_OWN_BUCKET_PHASES = {"opening", "morning", "midday", "afternoon", "closing"}
_OFFHOURS_PHASES = {"pre_open", "post_close"}
_EXPIRY_PHASES = {"afternoon", "closing"}


def _expected_session_value(decision: dict) -> str:
    """Independently re-derive the design's (Session_Phase x expiry-day) collapse.

    Mirrors the design table: an expiry-day ``afternoon``/``closing`` candle ->
    ``expiry``; ``pre_open``/``post_close`` -> ``offhours``; each remaining
    in-session phase -> its own bucket; anything missing / unavailable /
    unrecognized -> ``unknown``.
    """
    d = decision or {}
    deff = d.get("defensibility") or {}
    sess = deff.get("session")
    if not isinstance(sess, dict):
        return "unknown"
    if sess.get("available") is False:
        return "unknown"
    phase = str(sess.get("session_phase") or "").strip().lower()
    if phase not in _OWN_BUCKET_PHASES and phase not in _OFFHOURS_PHASES:
        return "unknown"
    expiry_context = sess.get("expiry_context")
    is_expiry_day = (
        bool(expiry_context.get("is_expiry_day"))
        if isinstance(expiry_context, dict)
        else False
    )
    if is_expiry_day and phase in _EXPIRY_PHASES:
        return "expiry"
    if phase in _OFFHOURS_PHASES:
        return "offhours"
    return phase


@st.composite
def _expiry_context(draw):
    """An assorted ``expiry_context`` shape (well-formed, partial, or malformed)."""
    kind = draw(st.sampled_from(["wellformed", "missing_flag", "non_dict", "absent"]))
    if kind == "wellformed":
        return {
            "is_expiry_day": draw(st.booleans()),
            "days_until_expiry": draw(st.integers(min_value=0, max_value=6)),
        }
    if kind == "missing_flag":
        return {"days_until_expiry": draw(st.integers(min_value=0, max_value=6))}
    if kind == "non_dict":
        return draw(st.sampled_from([None, "thursday", 0, [1, 2], True]))
    return "__ABSENT__"


@st.composite
def _session_entry(draw):
    """A defensibility ``session`` entry spanning usable / unavailable / malformed."""
    kind = draw(
        st.sampled_from(
            [
                "label",          # usable Session_Label
                "label_avail",    # usable label with explicit available: True
                "unavailable",    # explicit available: False
                "unknown_phase",  # unrecognized phase string
                "missing_phase",  # no session_phase key
                "non_dict",       # session is not a dict
            ]
        )
    )
    if kind in ("label", "label_avail"):
        entry = {"session_phase": draw(st.sampled_from(_ALL_PHASES))}
        ec = draw(_expiry_context())
        if ec != "__ABSENT__":
            entry["expiry_context"] = ec
        if kind == "label_avail":
            entry["available"] = True
        # carry assorted extra fields that must be ignored by the tag derivation
        entry["minutes_since_open"] = draw(st.one_of(st.none(), st.floats(0, 400)))
        entry["time_favorability"] = draw(
            st.sampled_from(["favorable", "unfavorable", "neutral"])
        )
        return entry
    if kind == "unavailable":
        return {"available": False, "reason": "candle retrieval failed"}
    if kind == "unknown_phase":
        return {
            "session_phase": draw(
                st.sampled_from(["lunch", "", "OPENING ", "after-hours", "xyz"])
            ),
            "expiry_context": {"is_expiry_day": draw(st.booleans())},
        }
    if kind == "missing_phase":
        return {"expiry_context": {"is_expiry_day": draw(st.booleans())}}
    # non_dict
    return draw(st.sampled_from([None, "afternoon", 42, ["closing"], True]))


@st.composite
def _decision(draw):
    """A committed decision with an assorted defensibility/session shape."""
    shape = draw(
        st.sampled_from(
            [
                "with_session",   # defensibility carries a session entry
                "no_session",     # defensibility present but no session key
                "no_deff",        # no defensibility key at all
            ]
        )
    )
    action = draw(st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", None]))
    decision = {}
    if action is not None:
        decision["action"] = action
    if shape == "with_session":
        decision["defensibility"] = {"session": draw(_session_entry())}
    elif shape == "no_session":
        decision["defensibility"] = {"regime": {"available": False}}
    # shape == "no_deff": leave defensibility absent entirely
    return decision


# ─────────────────────────────────────────────────────────────────────────────
# Property 22: Exactly one low-cardinality session tag at a fixed position
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 22: Exactly one low-cardinality session tag at a fixed position
@settings(max_examples=300, deadline=None)
@given(decision=_decision())
def test_property_22_session_tag_low_cardinality_fixed_position(decision):
    """Validates: Requirements 10.1, 10.2, 10.3

    ``derive_setup_tags`` appends exactly one ``sess:<value>`` tag, drawn from the
    fixed at-most-8-value enumeration, at a fixed (last) position; a decision
    lacking a valid session entry yields ``sess:unknown``; and identical decisions
    yield an identical ``setup_key``.
    """
    # Static guard: the enumeration is low-cardinality (at most 8 incl. unknown).
    assert len(SESS_TAG_VALUES) <= 8, f"SESS_TAG_VALUES too large: {SESS_TAG_VALUES}"
    assert "unknown" in SESS_TAG_VALUES

    tags = derive_setup_tags(decision)

    # ── Exactly one session tag (R10.1). ─────────────────────────────────────
    sess_tags = [t for t in tags if t.startswith("sess:")]
    assert len(sess_tags) == 1, f"expected exactly one sess: tag, got {sess_tags}"
    sess_tag = sess_tags[0]
    value = sess_tag.split(":", 1)[1]

    # ── Value drawn from the fixed low-cardinality enumeration (R10.3). ───────
    assert value in SESS_TAG_VALUES, f"sess value {value!r} not in {SESS_TAG_VALUES}"

    # ── Fixed position: the session tag is the LAST tag in the sequence. ──────
    sess_index = tags.index(sess_tag)
    assert sess_index == len(tags) - 1, (
        f"sess tag not at fixed (last) position: index {sess_index} of {len(tags)}"
    )

    # ── Collapsing rules hold against the independent reference (R10.2/R10.3). ─
    expected = _expected_session_value(decision)
    assert value == expected, (
        f"sess value {value!r} != expected {expected!r} for decision {decision!r}"
    )

    # ── A decision lacking a valid session entry yields sess:unknown (R10.2). ─
    deff = decision.get("defensibility") or {}
    sess = deff.get("session")
    has_valid_entry = (
        isinstance(sess, dict)
        and sess.get("available") is not False
        and str(sess.get("session_phase") or "").strip().lower()
        in (_OWN_BUCKET_PHASES | _OFFHOURS_PHASES)
    )
    if not has_valid_entry:
        assert value == "unknown", (
            f"expected sess:unknown for invalid/absent entry, got {value!r}"
        )

    # ── setup_key determinism: identical input -> identical setup_key (R10.1). ─
    tags_again = derive_setup_tags(decision)
    assert setup_key_from_tags(tags) == setup_key_from_tags(tags_again)
    # A deep-equal copy of the decision yields the identical setup_key too.
    import copy

    key_copy = setup_key_from_tags(derive_setup_tags(copy.deepcopy(decision)))
    assert setup_key_from_tags(tags) == key_copy
