"""Property-based test for the regime verification step (stream_events.py, task 10.2).

Feature: regime-detection-gate

This module implements design **Property 21: Exactly one regime verification
step with the correct outcome mapping**:

    For any defensibility record, building the verification steps yields EXACTLY
    ONE step carrying the stable check id ``market-regime``, whose outcome maps
    the recorded regime Favorability as:

        favorable    -> pass            (R8.2)
        unfavorable  -> fail            (R8.3)
        neutral      -> informational   (R8.4)
        unavailable  -> not-evaluable (carrying an 'unavailable' indication;
                                        no fabricated favorability, R8.5)

    and the single step is present for every regime shape (R8.1).

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5.

The implementation under test lives in ``stream_events.py``:
  - ``build_verification_steps(decision)`` — FIND-mode records (no
    ``validator_checks``) route to ``_derive_find_mode_steps`` which appends
    exactly one ``_regime_step(record)``.
  - ``_regime_step(record)`` — maps the defensibility ``regime`` entry to a
    single step under the fixed check id ``market-regime``.

The real LLM / graph is never invoked. The defensibility ``regime`` entry is
built directly in the shape ``graph._regime_entry`` produces: a usable label
``{"available": True, "favorability": ..., "trend_state": ..., ...}`` or an
Unavailable_Marker ``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors ``tests/test_stream_events.py``: the
service directory (one level up) is prepended to ``sys.path`` so
``stream_events`` is importable when pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import build_verification_steps, _regime_step  # noqa: E402

REGIME_CHECK = "market-regime"

# The Favorability -> outcome mapping the step must implement (R8.2-R8.4).
_FAVORABILITY_OUTCOME = {
    "favorable": "pass",
    "unfavorable": "fail",
    "neutral": "informational",
}
# Outcomes that would betray a fabricated Favorability on the unavailable path.
_FABRICATED_OUTCOMES = set(_FAVORABILITY_OUTCOME.values())


# ── Strategies ───────────────────────────────────────────────────────────────
_trend_state = st.sampled_from(["trending", "ranging", "transitional"])
_volatility_state = st.sampled_from(["low", "normal", "high"])

_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _available_regime_entry(draw):
    """A usable regime entry across all three Favorability values (R8.2-R8.4)."""
    favorability = draw(st.sampled_from(["favorable", "unfavorable", "neutral"]))
    return {
        "available": True,
        "favorability": favorability,
        "trend_state": draw(_trend_state),
        "volatility_state": draw(_volatility_state),
        "measures": {
            "directional_strength": draw(_measure_value),
            "choppiness": draw(_measure_value),
            "efficiency_ratio": draw(_measure_value),
            "atr_percentile": draw(_measure_value),
            "bb_width": draw(_measure_value),
        },
    }


# An Unavailable_Marker entry: available False, only a reason (no states, R8.5).
_unavailable_reason = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "insufficient data: 18 valid candles received, 50 required",
            "candle retrieval timed out",
            "no regime measure could be computed",
        ]
    ),
)
_unavailable_regime_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    _unavailable_reason,
)

_regime_entry = st.one_of(_available_regime_entry(), _unavailable_regime_entry)

# Optional FIND-mode record fields the other checks read. Their presence/absence
# must not affect the single regime step. Crucially the record carries NO
# ``validator_checks`` so it routes through FIND mode.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)


def _only_regime_step(steps):
    """Return the single market-regime step, asserting exactly one exists (R8.1)."""
    regime_steps = [s for s in steps if s.get("check") == REGIME_CHECK]
    assert len(regime_steps) == 1, (
        f"expected exactly one '{REGIME_CHECK}' step, got {len(regime_steps)}"
    )
    return regime_steps[0]


# ─────────────────────────────────────────────────────────────────────────────
# Property 21: exactly one regime verification step with the correct outcome
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 21
@settings(max_examples=200, deadline=None)
@given(
    regime=_regime_entry,
    extras=_find_mode_extras,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_21_regime_verification_step_outcome_mapping(regime, extras, action):
    """Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5

    For any regime entry shape (each Favorability value or unavailable), building
    the FIND-mode verification steps yields exactly ONE step with check id
    ``market-regime`` whose outcome maps favorability correctly, and for the
    unavailable case carries an 'unavailable' indication with no fabricated
    favorability.
    """
    record = dict(extras)
    record["regime"] = regime
    decision = {"action": action, "defensibility": record}

    steps = build_verification_steps(decision)

    # ── R8.1: exactly one regime step under the stable check id ──────────────
    step = _only_regime_step(steps)
    assert step["check"] == REGIME_CHECK
    outcome = step.get("outcome")
    assert outcome  # always present

    if regime.get("available") and regime.get("favorability") in _FAVORABILITY_OUTCOME:
        # ── R8.2 / R8.3 / R8.4: favorability maps to the exact outcome ───────
        expected = _FAVORABILITY_OUTCOME[regime["favorability"]]
        assert outcome == expected, (
            f"favorability={regime['favorability']} -> outcome {outcome!r}, "
            f"expected {expected!r}"
        )
    else:
        # ── R8.5: unavailable -> not-evaluable, no fabricated favorability ───
        assert outcome.startswith("not-evaluable"), (
            f"unavailable regime must report not-evaluable, got {outcome!r}"
        )
        assert "unavailable" in outcome, (
            f"unavailable regime outcome must carry an 'unavailable' indication, "
            f"got {outcome!r}"
        )
        # No fabricated pass/fail/informational outcome on the unavailable path.
        assert outcome not in _FABRICATED_OUTCOMES
        # And the step never invents a favorability field.
        assert "favorability" not in step


# ── Cross-check the helper directly across every Favorability value (R8.2-8.4) ─

# Feature: regime-detection-gate, Property 21
@settings(max_examples=200, deadline=None)
@given(entry=_available_regime_entry())
def test_property_21_regime_step_helper_maps_each_favorability(entry):
    """Validates: Requirements 8.1, 8.2, 8.3, 8.4

    ``_regime_step`` directly maps each available Favorability to its outcome
    under the stable ``market-regime`` check id.
    """
    step = _regime_step({"regime": entry})
    assert step["check"] == REGIME_CHECK
    assert step["outcome"] == _FAVORABILITY_OUTCOME[entry["favorability"]]
