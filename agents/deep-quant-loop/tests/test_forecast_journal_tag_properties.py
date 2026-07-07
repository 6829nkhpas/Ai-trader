"""Property-based test for the journal forecast setup-fingerprint tag.

Feature: volatility-aware-forecaster (journal.py, task 12.3)

This module implements design **Property 28: Exactly one low-cardinality forecast
tag at a fixed position**:

    For any committed decision, ``journal.derive_setup_tags`` appends EXACTLY ONE
    ``fc:<value>`` tag, whose ``<value>`` is drawn from the fixed
    ``journal.FC_TAG_VALUES`` enumeration (at most 8 values, including
    ``unknown``). The tag sits at a FIXED position (immediately after the
    ``rs:`` tag, i.e. last) so the derived ``setup_key`` is deterministic for
    identical inputs. A missing, unavailable, empty, non-numeric-probability, or
    unrecognized-alignment forecast entry collapses to ``fc:unknown``.

Validates: Requirements 11.1, 11.2, 11.3.

The implementation under test lives in ``journal.py``:
  - ``derive_setup_tags(decision)`` — builds the coarse setup fingerprint and
    appends the forecast tag last (after the ``rs:`` tag).
  - ``_forecast_tag(decision)`` — collapses (Forecast_Alignment x Up_Probability
    confidence band) into one fixed enumeration value, with the band split at
    ``FC_STRONG_PROB_SPLIT`` (0.15) from a 0.5 coin flip.
  - ``setup_key_from_tags(tags)`` — joins the tags into the deterministic key.
  - ``FC_TAG_VALUES`` — the fixed enumeration.
  - ``FC_STRONG_PROB_SPLIT`` — the strong/weak band split.

The sys.path / import pattern mirrors the sibling relative-strength property test
(``test_rs_setup_tag_properties.py``) in this directory: the service directory
(one level up) is prepended to ``sys.path`` so ``journal`` is importable when
pytest is run from anywhere.
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
    FC_TAG_VALUES,
    FC_STRONG_PROB_SPLIT,
)

# Categorical forecast alignment field (the forecaster's enum).
_ALIGNMENTS = ["aligned", "misaligned", "neutral"]


def _expected_fc_value(alignment: str, up_probability: float) -> str:
    """Mirror ``journal._forecast_tag`` collapse logic for valid inputs.

    The Alignment is the prefix; the Up_Probability confidence band is the
    suffix: ``strong`` when ``abs(up_probability - 0.5) >= FC_STRONG_PROB_SPLIT``,
    else ``weak``. Any value not in the enumeration collapses to ``unknown``.
    """
    band = "strong" if abs(up_probability - 0.5) >= FC_STRONG_PROB_SPLIT else "weak"
    value = f"{alignment}-{band}"
    return value if value in FC_TAG_VALUES else "unknown"


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

# A regime entry so the regime: tag is present.
_regime_entry = st.sampled_from([
    {"available": True, "trend_state": "trending", "favorability": "favorable"},
    {"available": True, "trend_state": "ranging", "favorability": "neutral"},
    {"available": False, "reason": "insufficient data"},
    None,
])

# A relative-strength entry so the rs: tag is present (fc: must sit immediately
# after it, i.e. last).
_rs_entry = st.sampled_from([
    {"available": True, "relative_strength_state": "leader", "alignment": "aligned"},
    {"available": True, "relative_strength_state": "laggard", "alignment": "misaligned"},
    {"available": False, "reason": "missing benchmark"},
    None,
])

_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)

# Up_Probability strategy spanning [0, 1] AND straddling the strong/weak split.
# 0.5 +/- FC_STRONG_PROB_SPLIT (= 0.35 / 0.65) are the band boundaries; sampling
# values on, just inside, and just outside the band exercises both branches.
_split = FC_STRONG_PROB_SPLIT
_up_probability = st.one_of(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([
        0.0, 1.0, 0.5,                       # extremes + exact coin flip
        0.5 - _split, 0.5 + _split,          # exactly on the band boundary (strong)
        0.5 - _split + 1e-9, 0.5 + _split - 1e-9,  # just inside the band (weak)
        0.5 - _split - 1e-9, 0.5 + _split + 1e-9,  # just outside the band (strong)
    ]),
)


@st.composite
def _valid_forecast(draw):
    """An available forecast entry with a recognized alignment and numeric prob.

    Returns (forecast_entry, expected_tag_value)."""
    alignment = draw(st.sampled_from(_ALIGNMENTS))
    up_probability = draw(_up_probability)
    entry = {
        "available": True,
        "projected_direction": draw(st.sampled_from(["up", "down", "flat"])),
        "up_probability": up_probability,
        "expected_move_atr": draw(_measure_value),
        "forecast_confidence": draw(st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)),
        "forecast_alignment": alignment,
        "measures": {
            "drift": draw(_measure_value),
            "volatility": draw(_measure_value),
            "standardized_drift": draw(_measure_value),
            "atr": draw(_measure_value),
        },
    }
    return entry, _expected_fc_value(alignment, up_probability)


@st.composite
def _unknown_forecast(draw):
    """A forecast entry that must collapse to ``unknown``.

    Covers missing/None/non-dict/unavailable/empty/non-numeric-probability/
    unrecognized-alignment cases. Returns (forecast_entry, "unknown")."""
    kind = draw(st.sampled_from([
        "missing",       # no forecast key at all
        "none",          # forecast is None
        "not_dict",      # forecast is a non-dict value
        "unavailable",   # explicit available=False
        "empty",         # available but blank fields
        "bad_align",     # unrecognized forecast_alignment
        "non_numeric",   # alignment valid but up_probability not a finite number
        "garbage",       # arbitrary junk values
    ]))
    if kind == "missing":
        return "__MISSING__", "unknown"
    if kind == "none":
        return None, "unknown"
    if kind == "not_dict":
        return draw(st.sampled_from(["unavailable", 42, ["x"]])), "unknown"
    if kind == "unavailable":
        # An explicitly unavailable forecast (even if it leaks fields) -> unknown.
        return {
            "available": False,
            "reason": "insufficient data",
            "forecast_alignment": draw(st.sampled_from(_ALIGNMENTS)),
            "up_probability": draw(st.floats(
                min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)),
        }, "unknown"
    if kind == "empty":
        return {"available": True, "forecast_alignment": "", "up_probability": None}, "unknown"
    if kind == "bad_align":
        return {
            "available": True,
            "forecast_alignment": draw(st.sampled_from(["up", "down", "with", "against", ""])),
            "up_probability": draw(st.floats(
                min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)),
        }, "unknown"
    if kind == "non_numeric":
        return {
            "available": True,
            "forecast_alignment": draw(st.sampled_from(_ALIGNMENTS)),
            "up_probability": draw(st.sampled_from([None, "0.7", True, False, [], {}])),
        }, "unknown"
    # garbage
    return {
        "available": True,
        "forecast_alignment": draw(st.sampled_from([None, 7, [], {}])),
        "up_probability": draw(st.sampled_from([None, 9, (), {}])),
    }, "unknown"


@st.composite
def _decision(draw):
    """Draw a decision plus the EXPECTED forecast tag value.

    Mixes valid and unknown-collapsing forecast entries with the other
    defensibility fields that drive the macro/pred/va/regime/rs tags."""
    fc, expected = draw(st.one_of(_valid_forecast(), _unknown_forecast()))
    deff = {
        "macro_trend_conflict": draw(_macro),
        "predictive_conflict": draw(_predictive),
        "volume_profile": {"price_vs_value_area": draw(_value_area)},
        "regime": draw(_regime_entry),
        "relative_strength": draw(_rs_entry),
    }
    if fc != "__MISSING__":
        deff["forecast"] = fc
    decision = {"action": draw(_action), "defensibility": deff}
    return decision, expected


# ── Static invariant: the enumeration itself is small and contains ``unknown`` ─

def test_fc_tag_values_is_low_cardinality_and_contains_unknown():
    """Validates: Requirements 11.3 — fixed enumeration, <= 8 values incl unknown."""
    assert len(FC_TAG_VALUES) <= 8
    assert "unknown" in FC_TAG_VALUES


# ─────────────────────────────────────────────────────────────────────────────
# Property 28: exactly one low-cardinality forecast tag at a fixed position
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 28: Exactly one low-cardinality forecast tag at a fixed position
@settings(max_examples=100, deadline=None)
@given(payload=_decision())
def test_property_28_single_fixed_position_low_cardinality_fc_tag(payload):
    """Validates: Requirements 11.1, 11.2, 11.3

    derive_setup_tags appends exactly ONE ``fc:<value>`` tag, drawn from the
    fixed FC_TAG_VALUES enumeration, at a fixed position (last, immediately after
    the ``rs:`` tag); a missing/unavailable/non-numeric/unrecognized forecast
    entry collapses to ``fc:unknown`` while an available entry maps to the
    (alignment x probability-band) value; and the result (tags + setup_key) is
    deterministic for identical inputs.
    """
    decision, expected_value = payload

    tags = derive_setup_tags(decision)

    # ── Exactly ONE forecast tag (R11.1) ──────────────────────────────────────
    fc_tags = [t for t in tags if t.startswith("fc:")]
    assert len(fc_tags) == 1

    # ── Its value is in the fixed low-cardinality enumeration (R11.3) ──────────
    value = fc_tags[0][len("fc:"):]
    assert value in FC_TAG_VALUES
    assert len(FC_TAG_VALUES) <= 8 and "unknown" in FC_TAG_VALUES
    # Low-cardinality: at most 8 distinct fc values can ever be produced.
    assert len({v for v in FC_TAG_VALUES}) <= 8

    # ── Missing/unavailable/unrecognized -> fc:unknown; valid -> mapped (R11.2) ─
    assert value == expected_value

    # ── Fixed position: fc tag sits immediately after the ``rs:`` tag and
    #    immediately before the ``tm:`` tag. Later dimensions (tm/sess/db/opt/
    #    tier) and the event ``evt:`` tag are appended after it, so the ``evt:``
    #    tag is now the FINAL tag (R11.1). ──────────────────────────────────
    fc_index = tags.index(fc_tags[0])
    # The tag immediately before the fc tag is the relative-strength tag.
    assert tags[fc_index - 1].startswith("rs:")
    # The tag immediately after the fc tag is the management-style (tm:) tag.
    assert tags[fc_index + 1].startswith("tm:")
    # The event-date risk dimension is always appended last (after tier:), per
    # earnings-event-risk-gate R10.1; the opportunity-tier dimension is now
    # second-to-last.
    assert tags[-1].startswith("evt:")
    assert tags[-2].startswith("tier:")

    # ── Determinism: identical inputs -> identical tag list and setup_key (R11.1) ─
    tags_again = derive_setup_tags(decision)
    assert tags_again == tags
    assert setup_key_from_tags(tags_again) == setup_key_from_tags(tags)
    # The fc value occupies the same deterministic slot in setup_key, immediately
    # after the rs: component; the evt: component is always last (tier: second-to-last).
    key_parts = setup_key_from_tags(tags).split("|")
    assert key_parts[-1].startswith("evt:")
    assert key_parts[-2].startswith("tier:")
    fc_key_index = key_parts.index(fc_tags[0])
    assert key_parts[fc_key_index - 1].startswith("rs:")
