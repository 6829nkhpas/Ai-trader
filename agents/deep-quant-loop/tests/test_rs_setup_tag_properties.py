"""Property-based test for the journal relative-strength setup-fingerprint tag.

Feature: relative-strength-context (journal.py, task 11.2)

This module implements design **Property 25: Exactly one low-cardinality
relative-strength tag at a fixed position**:

    For any committed decision, ``journal.derive_setup_tags`` appends EXACTLY ONE
    ``rs:<value>`` tag, whose ``<value>`` is drawn from the fixed
    ``journal.RS_TAG_VALUES`` enumeration (at most 8 values, including
    ``unknown``). The tag sits at a FIXED position (immediately after the
    ``regime:`` tag, i.e. last) so the derived ``setup_key`` is deterministic for
    identical inputs. A missing, unavailable, empty, or unrecognized
    relative-strength entry collapses to ``rs:unknown``.

Validates: Requirements 10.1, 10.2, 10.3.

The implementation under test lives in ``journal.py``:
  - ``derive_setup_tags(decision)`` — builds the coarse setup fingerprint and
    appends the relative-strength tag last (after the ``regime:`` tag).
  - ``_relative_strength_tag(decision)`` — collapses (Relative_Strength_State x
    Alignment) into one fixed enumeration value.
  - ``setup_key_from_tags(tags)`` — joins the tags into the deterministic key.
  - ``RS_TAG_VALUES`` — the fixed enumeration.

The sys.path / import pattern mirrors the other relative-strength property tests
in this directory: the service directory (one level up) is prepended to
``sys.path`` so ``journal`` is importable when pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (journal.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
from journal import (  # noqa: E402
    derive_setup_tags,
    setup_key_from_tags,
    RS_TAG_VALUES,
)

# Categorical relative-strength fields (the classifier's enums).
_RS_STATES = ["leader", "inline", "laggard"]
_ALIGNMENTS = ["aligned", "misaligned", "neutral"]


def _expected_rs_value(state: str, alignment: str) -> str:
    """Mirror ``journal._relative_strength_tag`` collapse logic for valid inputs.

    Directional pairings and ``inline-neutral`` collapse to ``<state>-<alignment>``;
    residual combinations collapse to the bare Alignment when that Alignment is
    itself an enumeration value; everything else collapses to ``unknown``.
    """
    value = f"{state}-{alignment}"
    if value in RS_TAG_VALUES:
        return value
    if alignment in RS_TAG_VALUES:
        return alignment
    return "unknown"


# ── Strategies for the other defensibility fields (drive the other tags) ─────
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

_macro = st.sampled_from([
    "Macro conflict: trade opposes the 1d trend",
    "Trade aligned with the 1d trend",
    "1d trend unavailable",
    "",
    None,
])
_predictive = st.sampled_from([
    "CONFLICT: forward projection opposes the trade bias",
    "No predictive conflict: projection aligns with trade bias",
    "",
    None,
])
_value_area = st.sampled_from([
    "above_value_area", "inside_value_area", "below_value_area", "unknown", None,
])

# A regime entry so the regime: tag is present (rs: must sit immediately after).
_regime_entry = st.sampled_from([
    {"available": True, "trend_state": "trending", "favorability": "favorable"},
    {"available": True, "trend_state": "ranging", "favorability": "neutral"},
    {"available": False, "reason": "insufficient data"},
    None,
])

_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _valid_rs(draw):
    """An available relative-strength entry with recognized state/alignment values.

    Returns (rs_entry, expected_tag_value)."""
    state = draw(st.sampled_from(_RS_STATES))
    alignment = draw(st.sampled_from(_ALIGNMENTS))
    entry = {
        "available": True,
        "benchmark": draw(st.sampled_from(["NIFTY", "BANKNIFTY", "SPX"])),
        "index_direction": draw(st.sampled_from(["up", "down", "flat"])),
        "relative_strength_state": state,
        "alignment": alignment,
        "measures": {
            "rs_ratio_slope": draw(_measure_value),
            "relative_return": draw(_measure_value),
            "correlation": draw(_measure_value),
            "beta": draw(_measure_value),
            "index_return": draw(_measure_value),
        },
    }
    return entry, _expected_rs_value(state, alignment)


@st.composite
def _unknown_rs(draw):
    """A relative-strength entry that must collapse to ``unknown``.

    Covers missing/None/non-dict/unavailable/empty/garbage cases.
    Returns (rs_entry, "unknown")."""
    kind = draw(st.sampled_from([
        "missing",       # no relative_strength key at all
        "none",          # relative_strength is None
        "not_dict",      # relative_strength is a non-dict value
        "unavailable",   # explicit available=False
        "empty",         # available but blank fields
        "bad_state",     # unrecognized relative_strength_state
        "bad_align",     # unrecognized alignment
        "garbage",       # arbitrary junk values
    ]))
    if kind == "missing":
        return "__MISSING__", "unknown"
    if kind == "none":
        return None, "unknown"
    if kind == "not_dict":
        return draw(st.sampled_from(["unavailable", 42, ["x"]])), "unknown"
    if kind == "unavailable":
        return {"available": False, "reason": "missing benchmark"}, "unknown"
    if kind == "empty":
        return {"available": True, "relative_strength_state": "", "alignment": ""}, "unknown"
    if kind == "bad_state":
        return {
            "available": True,
            "relative_strength_state": draw(st.sampled_from(["strong", "weak", "bull"])),
            "alignment": draw(st.sampled_from(_ALIGNMENTS)),
        }, "unknown"
    if kind == "bad_align":
        return {
            "available": True,
            "relative_strength_state": draw(st.sampled_from(_RS_STATES)),
            "alignment": draw(st.sampled_from(["with", "against", "sideways", ""])),
        }, "unknown"
    # garbage
    return {
        "available": True,
        "relative_strength_state": draw(st.sampled_from([None, 7, [], {}])),
        "alignment": draw(st.sampled_from([None, 9, (), {}])),
    }, "unknown"


@st.composite
def _decision(draw):
    """Draw a decision plus the EXPECTED relative-strength tag value.

    Mixes valid and unknown-collapsing relative-strength entries with the other
    defensibility fields that drive the macro/pred/va/regime tags."""
    rs, expected = draw(st.one_of(_valid_rs(), _unknown_rs()))
    deff = {
        "macro_trend_conflict": draw(_macro),
        "predictive_conflict": draw(_predictive),
        "volume_profile": {"price_vs_value_area": draw(_value_area)},
        "regime": draw(_regime_entry),
    }
    if rs != "__MISSING__":
        deff["relative_strength"] = rs
    decision = {"action": draw(_action), "defensibility": deff}
    return decision, expected


# ── Static invariant: the enumeration itself is small and contains ``unknown`` ─

def test_rs_tag_values_is_low_cardinality_and_contains_unknown():
    """Validates: Requirements 10.3 — fixed enumeration, <= 8 values incl unknown."""
    assert len(RS_TAG_VALUES) <= 8
    assert "unknown" in RS_TAG_VALUES


# ─────────────────────────────────────────────────────────────────────────────
# Property 25: exactly one low-cardinality relative-strength tag at a fixed position
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 25: Exactly one low-cardinality relative-strength tag at a fixed position
@settings(max_examples=100, deadline=None)
@given(payload=_decision())
def test_property_25_single_fixed_position_low_cardinality_rs_tag(payload):
    """Validates: Requirements 10.1, 10.2, 10.3

    derive_setup_tags appends exactly ONE ``rs:<value>`` tag, drawn from the
    fixed RS_TAG_VALUES enumeration, at a fixed position (immediately after the
    ``regime:`` tag); a missing/unavailable/unrecognized relative-strength entry
    collapses to ``rs:unknown``; and the result (tags + setup_key) is
    deterministic for identical inputs.
    """
    decision, expected_value = payload

    tags = derive_setup_tags(decision)

    # ── Exactly ONE relative-strength tag (R10.1) ─────────────────────────────
    rs_tags = [t for t in tags if t.startswith("rs:")]
    assert len(rs_tags) == 1

    # ── Its value is in the fixed low-cardinality enumeration (R10.3) ─────────
    value = rs_tags[0][len("rs:"):]
    assert value in RS_TAG_VALUES
    assert len(RS_TAG_VALUES) <= 8 and "unknown" in RS_TAG_VALUES
    # Low-cardinality: at most 8 distinct rs values can ever be produced.
    assert len({v for v in RS_TAG_VALUES}) <= 8

    # ── Missing/unavailable/unrecognized -> rs:unknown; valid -> mapped (R10.2) ─
    assert value == expected_value

    # ── Fixed position: rs tag is LAST, immediately after the ``regime:`` tag (R10.1) ─
    assert tags[-1] == rs_tags[0]
    assert tags[-1].startswith("rs:")
    # The tag immediately before the rs tag is the regime tag.
    assert tags[-2].startswith("regime:")

    # ── Determinism: identical inputs -> identical tag list and setup_key (R10.1) ─
    tags_again = derive_setup_tags(decision)
    assert tags_again == tags
    assert setup_key_from_tags(tags_again) == setup_key_from_tags(tags)
    # The rs value occupies the same (last) deterministic slot in setup_key.
    key = setup_key_from_tags(tags)
    assert key.split("|")[-1] == rs_tags[0]
