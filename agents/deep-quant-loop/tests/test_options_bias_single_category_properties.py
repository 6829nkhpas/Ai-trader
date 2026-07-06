"""Property-based test for the Options_Bias_State category (options_bias.py, task 2.2).

Feature: options-agent-integration

This module implements design **Property 1: Bias state is always a single valid
category**:

    For ANY analytics input — well-formed, partially-formed, all-null, or
    outright malformed/garbage — ``classify_options_bias`` returns a label whose
    ``options_bias_state`` is exactly one of ``bullish`` / ``bearish`` /
    ``neutral`` (the ``OPTIONS_BIAS_STATES`` enumeration). It is never absent,
    never ``None``, never a fabricated extra category, and the classifier never
    raises.

Validates: Requirements 1.1.

The classifier is the single source of truth for the options-bias category, so
this property exercises ``classify_options_bias`` directly over a generator that
deliberately spans the full input space: each analytics field (``pcr_oi``,
``oi_buildup``, ``max_pain``, ``spot``, ``oi_walls``, ``iv_skew``,
``futures_basis``) independently takes valid, ``null``, or garbage values, and
the analytics container itself may be a non-dict. The sys.path / import pattern
mirrors the sibling ``test_options_*_properties.py`` / ``test_of_*_properties.py``
modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options_bias.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options_bias import (  # noqa: E402
    OPTIONS_BIAS_STATES,
    classify_options_bias,
    resolve_options_bias_config,
)

# Resolve config once (deterministic, env-independent for the default path).
_CONFIG = resolve_options_bias_config()

_VALID_STATES = set(OPTIONS_BIAS_STATES)  # {"bullish", "bearish", "neutral"}


# ── Smart generators spanning the FULL analytics input space ──────────────────
# Each numeric analytic must tolerate finite numbers, ``None``, non-finite floats,
# and outright non-numeric junk, so the scalar generator mixes all of them.
_weird_number = st.one_of(
    st.none(),
    st.floats(min_value=-1_000_000.0, max_value=1_000_000.0,
              allow_nan=False, allow_infinity=False),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.just(0.0),
    st.text(max_size=4),   # non-numeric junk
    st.booleans(),         # bool is explicitly excluded by the classifier
)

# PCR(OI) leans positive in practice but the classifier must tolerate anything.
_weird_pcr = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    _weird_number,
)

# An OI-buildup label: the five F2 categories plus junk / non-strings.
_buildup_label = st.one_of(
    st.sampled_from([
        "long_buildup", "short_buildup", "short_covering",
        "long_unwinding", "neutral",
    ]),
    st.text(max_size=5),
    st.none(),
    st.integers(),
)

# The aggregate oi_buildup field: a well-formed {"call","put"} object, a partial
# object, a junk dict, a non-dict, or absent.
_weird_oi_buildup = st.one_of(
    st.none(),
    st.fixed_dictionaries({"call": _buildup_label, "put": _buildup_label}),
    st.dictionaries(keys=st.text(max_size=4), values=_buildup_label, max_size=3),
    st.text(max_size=4),
    st.integers(),
)

# The oi_walls field: a well-formed {"support","resistance"} object, partial /
# junk dict, a non-dict, or absent.
_weird_oi_walls = st.one_of(
    st.none(),
    st.fixed_dictionaries({"support": _weird_number, "resistance": _weird_number}),
    st.dictionaries(keys=st.text(max_size=4), values=_weird_number, max_size=3),
    st.text(max_size=4),
    st.integers(),
)

# The iv_skew field: a well-formed object carrying put_minus_call, a junk dict, a
# non-dict, or absent.
_weird_iv_skew = st.one_of(
    st.none(),
    st.fixed_dictionaries({"put_minus_call": _weird_number}),
    st.dictionaries(keys=st.text(max_size=4), values=_weird_number, max_size=3),
    st.text(max_size=4),
    st.integers(),
)


@st.composite
def _weird_analytics(draw):
    """An analytics dict spanning well-formed, partial, all-null, and garbage.

    Every field independently takes a valid / null / garbage value so the whole
    input space — including a dict missing fields entirely — is reachable.
    """
    base = {
        "pcr_oi": draw(_weird_pcr),
        "oi_buildup": draw(_weird_oi_buildup),
        "max_pain": draw(_weird_number),
        "spot": draw(_weird_number),
        "oi_walls": draw(_weird_oi_walls),
        "iv_skew": draw(_weird_iv_skew),
        "futures_basis": draw(_weird_number),
    }
    # Randomly drop a subset of keys so "structurally absent" fields are exercised.
    drop = draw(st.sets(st.sampled_from(list(base.keys())), max_size=len(base)))
    return {k: v for k, v in base.items() if k not in drop}


# The analytics container itself may be a non-dict (malformed input) — the
# classifier must still return a neutral label rather than raising.
_analytics_or_garbage = st.one_of(
    _weird_analytics(),
    st.none(),
    st.text(max_size=5),
    st.integers(),
    st.lists(st.integers(), max_size=3),
)

# A proposed direction spanning recognized / unrecognized / absent / non-string.
_weird_direction = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", " hold ", "FLAT", ""]),
    st.text(max_size=5),
    st.integers(),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (task 2.2): Bias state is always a single valid category
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 1: Bias state is always a single valid category
@settings(max_examples=300, deadline=None)
@given(analytics=_analytics_or_garbage, proposed_direction=_weird_direction)
def test_property_1_bias_state_is_a_single_valid_category(analytics, proposed_direction):
    """Feature: options-agent-integration, Property 1: Bias state is always a
    single valid category.

    For any analytics input (well-formed or malformed) and any proposed
    direction, ``classify_options_bias`` returns a dict whose
    ``options_bias_state`` is exactly one of ``OPTIONS_BIAS_STATES``
    (``bullish`` / ``bearish`` / ``neutral``), and never raises.

    Validates: Requirements 1.1
    """
    label = classify_options_bias(analytics, _CONFIG, proposed_direction=proposed_direction)

    assert isinstance(label, dict), f"expected a label dict, got {label!r}"
    assert "options_bias_state" in label, f"label missing options_bias_state: {label!r}"

    state = label["options_bias_state"]
    assert state in _VALID_STATES, (
        f"options_bias_state {state!r} not in the valid category set {_VALID_STATES}"
    )
