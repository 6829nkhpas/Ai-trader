"""Property-based test for the journal regime setup-fingerprint tag (journal.py, task 11.2).

Feature: regime-detection-gate

This module implements design **Property 23: Exactly one low-cardinality regime
tag at a fixed position**:

    For any committed decision, ``journal.derive_setup_tags`` appends EXACTLY ONE
    ``regime:<value>`` tag, whose ``<value>`` is drawn from the fixed
    ``journal.REGIME_TAG_VALUES`` enumeration (at most 8 values, including
    ``unknown``). The tag sits at a FIXED position (always last, after the
    ``va:`` tag) so the derived ``setup_key`` is deterministic for identical
    inputs. A missing, unavailable, empty, or unrecognized regime collapses to
    ``regime:unknown``.

Validates: Requirements 9.1, 9.2, 9.3.

The implementation under test lives in ``journal.py``:
  - ``derive_setup_tags(decision)`` — builds the coarse setup fingerprint and
    appends the regime tag last.
  - ``_regime_tag(decision)`` — collapses (Trend_State x Favorability) into one
    fixed enumeration value.
  - ``setup_key_from_tags(tags)`` — joins the tags into the deterministic key.
  - ``REGIME_TAG_VALUES`` — the fixed enumeration.

The sys.path / import pattern mirrors the other regime property tests in this
directory: the service directory (one level up) is prepended to ``sys.path`` so
``journal`` is importable when pytest is run from anywhere.
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
    REGIME_TAG_VALUES,
)

# Categorical regime fields (the classifier's enums).
_TREND_STATES = ["trending", "ranging", "transitional"]
_VOLATILITY_STATES = ["low", "normal", "high"]
_FAVORABILITY = ["favorable", "unfavorable", "neutral"]

# Trend_State -> tag family used to compute the EXPECTED tag for valid entries.
_EXPECTED_FAMILY = {"trending": "trend", "transitional": "trend", "ranging": "range"}


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

_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _valid_regime(draw):
    """An available regime entry with recognized trend/favorability values.

    Returns (regime_entry, expected_tag_value)."""
    trend = draw(st.sampled_from(_TREND_STATES))
    fav = draw(st.sampled_from(_FAVORABILITY))
    entry = {
        "available": True,
        "trend_state": trend,
        "volatility_state": draw(st.sampled_from(_VOLATILITY_STATES)),
        "favorability": fav,
        "measures": {
            "directional_strength": draw(_measure_value),
            "choppiness": draw(_measure_value),
            "efficiency_ratio": draw(_measure_value),
            "atr_percentile": draw(_measure_value),
            "bb_width": draw(_measure_value),
        },
    }
    expected = f"{_EXPECTED_FAMILY[trend]}-{fav}"
    return entry, expected


@st.composite
def _unknown_regime(draw):
    """A regime entry that must collapse to ``unknown`` (missing/unavailable/bad).

    Returns (regime_entry, "unknown")."""
    kind = draw(st.sampled_from([
        "missing",        # no regime key at all
        "none",           # regime is None
        "not_dict",       # regime is a non-dict value
        "unavailable",    # explicit available=False
        "empty",          # available but blank fields
        "bad_trend",      # unrecognized trend_state
        "bad_fav",        # unrecognized favorability
    ]))
    if kind == "missing":
        return "__MISSING__", "unknown"
    if kind == "none":
        return None, "unknown"
    if kind == "not_dict":
        return draw(st.sampled_from(["unavailable", 42, ["x"]])), "unknown"
    if kind == "unavailable":
        return {"available": False, "reason": "insufficient data"}, "unknown"
    if kind == "empty":
        return {"available": True, "trend_state": "", "favorability": ""}, "unknown"
    if kind == "bad_trend":
        return {
            "available": True,
            "trend_state": draw(st.sampled_from(["sideways", "choppy", "bull"])),
            "favorability": draw(st.sampled_from(_FAVORABILITY)),
        }, "unknown"
    # bad_fav
    return {
        "available": True,
        "trend_state": draw(st.sampled_from(_TREND_STATES)),
        "favorability": draw(st.sampled_from(["good", "bad", "sideways", ""])),
    }, "unknown"


@st.composite
def _decision(draw):
    """Draw a decision plus the EXPECTED regime tag value.

    Mixes valid and unknown-collapsing regime entries with the other
    defensibility fields that drive the macro/pred/va tags."""
    regime, expected = draw(st.one_of(_valid_regime(), _unknown_regime()))
    deff = {
        "macro_trend_conflict": draw(_macro),
        "predictive_conflict": draw(_predictive),
        "volume_profile": {"price_vs_value_area": draw(_value_area)},
    }
    if regime != "__MISSING__":
        deff["regime"] = regime
    decision = {"action": draw(_action), "defensibility": deff}
    return decision, expected


# ── Static invariant: the enumeration itself is small and contains ``unknown`` ─

def test_regime_tag_values_is_low_cardinality_and_contains_unknown():
    """Validates: Requirements 9.3 — fixed enumeration, <= 8 values incl unknown."""
    assert len(REGIME_TAG_VALUES) <= 8
    assert "unknown" in REGIME_TAG_VALUES


# ─────────────────────────────────────────────────────────────────────────────
# Property 23: exactly one low-cardinality regime tag at a fixed position
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 23
@settings(max_examples=200, deadline=None)
@given(payload=_decision())
def test_property_23_single_fixed_position_low_cardinality_regime_tag(payload):
    """Validates: Requirements 9.1, 9.2, 9.3

    derive_setup_tags appends exactly ONE ``regime:<value>`` tag, drawn from the
    fixed REGIME_TAG_VALUES enumeration, at a fixed position (last); a
    missing/unavailable/unrecognized regime collapses to ``regime:unknown``; and
    the result (tags + setup_key) is deterministic for identical inputs.
    """
    decision, expected_value = payload

    tags = derive_setup_tags(decision)

    # ── Exactly ONE regime tag (R9.1) ─────────────────────────────────────────
    regime_tags = [t for t in tags if t.startswith("regime:")]
    assert len(regime_tags) == 1

    # ── Its value is in the fixed low-cardinality enumeration (R9.3) ──────────
    value = regime_tags[0][len("regime:"):]
    assert value in REGIME_TAG_VALUES
    assert len(REGIME_TAG_VALUES) <= 8 and "unknown" in REGIME_TAG_VALUES

    # ── Missing/unavailable/unrecognized -> regime:unknown; valid -> mapped (R9.2) ─
    assert value == expected_value

    # ── Fixed position: the regime tag sits immediately after the ``va:`` tag
    # (R9.1). Subsequent dimensions are appended after it at their own fixed
    # positions, so the deterministic tag order is:
    #   dir:, macro:, pred:, va:, regime:, rs:, fc:, tm:, sess:, db:
    # — the multi-agent-debate ``db:`` tag is now the FINAL tag. The invariant
    # this property guards is the regime tag's own neighbors: it immediately
    # follows the ``va:`` tag and immediately precedes the ``rs:`` tag.
    regime_index = tags.index(regime_tags[0])
    assert tags[regime_index - 1].startswith("va:")
    rs_tags = [t for t in tags if t.startswith("rs:")]
    # derive_setup_tags always appends an rs: tag immediately after regime.
    assert rs_tags
    assert tags[regime_index + 1] == rs_tags[0]
    # The debate dimension is always appended last.
    assert tags[-1].startswith("db:")

    # ── Determinism: identical inputs -> identical tag list and setup_key (R9.1) ─
    tags_again = derive_setup_tags(decision)
    assert tags_again == tags
    assert setup_key_from_tags(tags_again) == setup_key_from_tags(tags)
    # The regime value occupies a fixed deterministic slot in setup_key: the
    # db: component is always last, and the regime component precedes the rs
    # component (which immediately follows it).
    key = setup_key_from_tags(tags)
    key_parts = key.split("|")
    assert key_parts[-1].startswith("db:")
    assert key_parts.index(regime_tags[0]) < key_parts.index(rs_tags[0])
