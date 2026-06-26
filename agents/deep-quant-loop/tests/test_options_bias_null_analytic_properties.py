"""Property-based test that a null analytic never contributes (options_bias.py, task 2.5).

Feature: options-agent-integration

This Hypothesis property exercises the pure Options_Bias_Classifier
(:func:`options_bias.classify_options_bias`) across arbitrary well-formed
``Options_Analytics_Result`` dicts and asserts design **Property 4: A null
analytic never contributes to the bias**:

    A ``null`` signal casts no vote — it is *excluded* from the bias rather than
    treated as a value (Requirement 1.4). A robust formulation: for any generated
    analytics dict, setting a single signal field to ``None`` must produce the
    *same* ``options_bias_state`` as removing that key entirely. In both cases the
    signal contributes no vote, so the netted bias state is identical. This holds
    for every votable signal: ``pcr_oi``, ``oi_buildup``, ``max_pain``,
    ``oi_walls``, ``iv_skew``, and ``futures_basis``.

    Validates: Requirements 1.4

The generator produces well-formed analytics dicts spanning the realistic input
space the classifier consumes. The sys.path / import pattern mirrors the sibling
``test_options_*_properties.py`` and ``test_of_*_properties.py`` modules.
"""

import copy
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
# ``None`` (the honest "absent" marker).
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

# The votable signal fields a null/absent value must equally exclude (R1.4).
_VOTABLE_FIELDS = st.sampled_from(
    ["pcr_oi", "oi_buildup", "max_pain", "oi_walls", "iv_skew", "futures_basis"]
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: A null analytic never contributes to the bias
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 4: A null analytic never contributes to the bias
@settings(max_examples=200, deadline=None)
@given(analytics=_analytics(), field=_VOTABLE_FIELDS, proposed_direction=_DIRECTION)
def test_property_4_null_analytic_never_contributes(
    analytics, field, proposed_direction
):
    """Feature: options-agent-integration, Property 4: A null analytic never
    contributes to the bias.

    For any well-formed analytics result and any single votable signal field,
    setting that field to ``None`` yields the SAME ``options_bias_state`` as
    removing the key entirely — i.e. a null signal casts no vote (it is excluded,
    not treated as a value). The classifier never mutates either input.

    Validates: Requirements 1.4
    """
    config = resolve_options_bias_config()
    assert isinstance(config, OptionsBiasConfig)

    # Variant A: the chosen signal field is explicitly null (None).
    analytics_null = copy.deepcopy(analytics)
    analytics_null[field] = None

    # Variant B: the chosen signal field's key is absent entirely.
    analytics_absent = copy.deepcopy(analytics)
    del analytics_absent[field]

    null_snapshot = copy.deepcopy(analytics_null)
    absent_snapshot = copy.deepcopy(analytics_absent)

    label_null = classify_options_bias(
        analytics_null, config, proposed_direction=proposed_direction
    )
    label_absent = classify_options_bias(
        analytics_absent, config, proposed_direction=proposed_direction
    )

    # The null signal contributes no vote either way: the netted bias state is
    # identical whether the field is None or simply not present.
    assert label_null["options_bias_state"] == label_absent["options_bias_state"], (
        f"null vs absent {field!r} produced different bias states: "
        f"{label_null['options_bias_state']!r} != "
        f"{label_absent['options_bias_state']!r}"
    )

    # The bias state must be a single valid category in both cases.
    assert label_null["options_bias_state"] in options_bias.OPTIONS_BIAS_STATES
    assert label_absent["options_bias_state"] in options_bias.OPTIONS_BIAS_STATES

    # The classifier is pure: neither analytics input is mutated.
    assert analytics_null == null_snapshot
    assert analytics_absent == absent_snapshot
