"""Property-based test for verbatim driving-signal echo (options_bias.py, task 2.4).

Feature: options-agent-integration

This Hypothesis property exercises the pure Options_Bias_Classifier
(:func:`options_bias.classify_options_bias`) across arbitrary well-formed
``Options_Analytics_Result`` dicts and asserts design **Property 3: The label
echoes its driving signals verbatim**:

    The ``signals`` object on the returned ``Options_Bias_Label`` carries the
    driving signals copied straight from the analytics input — the PCR value
    (``pcr_oi``), the aggregate ``oi_buildup``, the nearest ``oi_walls``, the
    max-pain position relative to spot, the IV skew (``put_minus_call``), and the
    ``futures_basis`` — never inferred or altered. The classifier is also pure:
    it produces no observable change to its analytics input (the dict remains
    deep-equal to a snapshot taken before the call).

    Validates: Requirements 1.3

The generator produces well-formed analytics dicts spanning the realistic input
space the classifier must echo: ``pcr_oi`` / ``max_pain`` / ``spot`` /
``futures_basis`` finite-or-null, the aggregate ``oi_buildup`` object with
``call`` / ``put`` labels drawn from the F2 OI-buildup vocabulary, the
``oi_walls`` object with numeric-or-null ``support`` / ``resistance``, and the
``iv_skew`` object with a numeric-or-null ``put_minus_call``. Both directional
and neutral biases are produced so the verbatim echo is exercised regardless of
the netted vote outcome.

The sys.path / import pattern mirrors the sibling ``test_options_*_properties.py``
and ``test_of_*_properties.py`` modules.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options_bias.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options_bias  # noqa: E402
from options_bias import (  # noqa: E402
    OptionsBiasConfig,
    classify_options_bias,
    resolve_options_bias_config,
)


# ── Smart generators constrained to a well-formed Options_Analytics_Result ────

# A finite-or-null numeric field: a finite real number (the normal case) OR
# ``None`` (the honest "absent" marker the classifier must echo straight
# through, never inferring a value).
_finite = st.floats(
    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
)
_numeric_or_null = st.one_of(st.none(), _finite)

# Spot is a strictly-positive finite price (a usable underlying spot).
_spot = st.floats(
    min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False
)

# PCR(OI) is a non-negative finite ratio (or null).
_pcr = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=20.0, allow_nan=False, allow_infinity=False),
)

# The five F2 OI-buildup category labels (plus the neutral / undefined reading).
_BUILDUP_LABELS = st.sampled_from(
    [
        "long_buildup",
        "short_buildup",
        "short_covering",
        "long_unwinding",
        "neutral",
    ]
)


@st.composite
def _oi_buildup(draw):
    """Aggregate OI-buildup object ``{"call": <label>, "put": <label>}``."""
    return {"call": draw(_BUILDUP_LABELS), "put": draw(_BUILDUP_LABELS)}


@st.composite
def _oi_walls(draw):
    """Nearest-OI-walls object with numeric-or-null support / resistance."""
    return {
        "support": draw(_numeric_or_null),
        "resistance": draw(_numeric_or_null),
    }


@st.composite
def _iv_skew(draw):
    """IV-skew object carrying a numeric-or-null ``put_minus_call``."""
    return {"put_minus_call": draw(_numeric_or_null)}


@st.composite
def _analytics(draw):
    """A well-formed Options_Analytics_Result the classifier consumes verbatim."""
    return {
        "underlying": draw(st.sampled_from(["NIFTY", "BANKNIFTY", "RELIANCE"])),
        "expiry": "2025-12-25",
        "spot": draw(_spot),
        "pcr_oi": draw(_pcr),
        "pcr_volume": draw(_pcr),
        "max_pain": draw(_numeric_or_null),
        "oi_buildup": draw(_oi_buildup()),
        "oi_walls": draw(_oi_walls()),
        "iv_skew": draw(_iv_skew()),
        "futures_basis": draw(_numeric_or_null),
    }


_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", "weird"]),
)


def _is_number(x):
    """True iff x is a real, finite number (mirrors options_bias._is_number)."""
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
    )


def _expected_max_pain_vs_spot(max_pain, spot):
    """Independently recompute the max-pain position relative to spot."""
    if not (_is_number(max_pain) and _is_number(spot)):
        return None
    if max_pain > spot:
        return "above"
    if max_pain < spot:
        return "below"
    return "at"


# ─────────────────────────────────────────────────────────────────────────────
# Property 3: The label echoes its driving signals verbatim
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 3: The label echoes its driving signals verbatim
@settings(max_examples=200, deadline=None)
@given(analytics=_analytics(), proposed_direction=_DIRECTION)
def test_property_3_label_echoes_driving_signals_verbatim(
    analytics, proposed_direction
):
    """Feature: options-agent-integration, Property 3: The label echoes its
    driving signals verbatim.

    For any well-formed analytics result, the returned label's ``signals`` object
    echoes — straight from the analytics input, never inferred or altered — the
    PCR value (``pcr_oi``), the aggregate ``oi_buildup``, the nearest ``oi_walls``,
    the max-pain position relative to spot, the IV skew (``put_minus_call``), and
    the ``futures_basis``. The classifier never mutates its analytics input.

    Validates: Requirements 1.3
    """
    config = resolve_options_bias_config()
    assert isinstance(config, OptionsBiasConfig)

    # Snapshot the input before the call to prove the classifier does not mutate it.
    analytics_snapshot = copy.deepcopy(analytics)

    label = classify_options_bias(
        analytics, config, proposed_direction=proposed_direction
    )

    signals = label["signals"]

    # PCR value echoed verbatim.
    assert signals["pcr_oi"] == analytics["pcr_oi"]

    # Aggregate OI buildup echoed verbatim (deep equal).
    assert signals["oi_buildup"] == analytics["oi_buildup"]

    # Nearest OI walls echoed verbatim (deep equal).
    assert signals["oi_walls"] == analytics["oi_walls"]

    # IV skew put_minus_call echoed verbatim.
    assert (
        signals["iv_skew_put_minus_call"]
        == analytics["iv_skew"]["put_minus_call"]
    )

    # Futures basis echoed verbatim.
    assert signals["futures_basis"] == analytics["futures_basis"]

    # Max pain and spot echoed verbatim, and the position derived (not inferred)
    # from those copied values.
    assert signals["max_pain"] == analytics["max_pain"]
    assert signals["spot"] == analytics["spot"]
    assert signals["max_pain_vs_spot"] == _expected_max_pain_vs_spot(
        analytics["max_pain"], analytics["spot"]
    )

    # Echoed nested objects must be copies, never aliases of the input objects,
    # so the label can never mutate the analytics through a shared reference.
    assert signals["oi_buildup"] is not analytics["oi_buildup"]
    assert signals["oi_walls"] is not analytics["oi_walls"]

    # The classifier is pure: the analytics input is unchanged (deep-equal).
    assert analytics == analytics_snapshot, (
        "classify_options_bias mutated its analytics input: "
        f"{analytics!r} != {analytics_snapshot!r}"
    )
