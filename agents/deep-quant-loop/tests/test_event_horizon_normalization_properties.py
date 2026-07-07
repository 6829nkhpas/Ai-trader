# Feature: earnings-event-risk-gate, Property 8: Holding_Horizon normalization is total
"""Property-based test for total Holding_Horizon normalization (events.py, task 2.10).

Feature: earnings-event-risk-gate

This module implements design **Property 8: Holding_Horizon normalization is
total**:

    For ANY ``value`` whatsoever — a recognized Holding_Horizon, an unrecognized
    string, the empty string, ``None``, a bool, an int/float, or any other
    non-string object — and any configuration, ``normalize_holding_horizon``
    returns a value drawn from ``HOLDING_HORIZONS``:

      * a recognized value (one of ``HOLDING_HORIZONS``) passes through unchanged
        (identity), and
      * anything else collapses to ``config.default_holding_horizon``.

    The function is *total*: it is defined for every possible input and never
    raises.

Validates: Requirements 3.2, 4.4.

The expected result is re-derived here by an INDEPENDENT reference rule (mirror
of the design mapping) rather than by calling the implementation, so this is a
genuine check that the implementation matches the specified normalization.

The sys.path / import pattern mirrors the sibling ``test_event_*`` modules.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from events import (  # noqa: E402
    HOLDING_HORIZONS,
    EventConfig,
    normalize_holding_horizon,
)

# The recognized Holding_Horizons the normalization is defined over.
_HORIZONS = sorted(HOLDING_HORIZONS)


# ─────────────────────────────────────────────────────────────────────────────
# Independent reference rule for the design's normalization (Requirements 3.2, 4.4)
# ─────────────────────────────────────────────────────────────────────────────


def _expected_horizon(value, config: EventConfig) -> str:
    """Re-derive the normalized Holding_Horizon from the design rule:

        value is a str in HOLDING_HORIZONS -> value (identity)
        anything else                      -> config.default_holding_horizon
    """
    if isinstance(value, str) and value in HOLDING_HORIZONS:
        return value
    return config.default_holding_horizon


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────


@st.composite
def _config(draw):
    """An arbitrary ``EventConfig`` whose ``default_holding_horizon`` is one of
    the recognized ``HOLDING_HORIZONS`` (the invariant ``resolve_event_config``
    guarantees). Only ``default_holding_horizon`` matters to this helper; other
    fields take arbitrary-but-valid values.
    """
    through = draw(st.integers(min_value=0, max_value=30))
    imminent = draw(st.integers(min_value=through, max_value=60))
    return EventConfig(
        enabled=draw(st.booleans()),
        timezone="Asia/Kolkata",
        default_holding_horizon=draw(st.sampled_from(_HORIZONS)),
        imminent_window_days=imminent,
        through_event_window_days=through,
        source_timeout_s=10.0,
        calendar_api_url=None,
        calendar_file_path=None,
    )


# Arbitrary values spanning the whole input space: recognized horizons, arbitrary
# text (incl. the empty string), None, bools, ints/floats (incl. non-finite),
# and assorted non-string containers.
_arbitrary_value = st.one_of(
    st.sampled_from(_HORIZONS),                       # recognized -> identity
    st.text(),                                        # arbitrary text incl. ""
    st.just(""),                                      # empty string explicitly
    st.none(),                                        # None
    st.booleans(),                                    # bool (a non-str)
    st.integers(),                                    # int
    st.floats(allow_nan=True, allow_infinity=True),   # float incl. NaN/inf
    st.lists(st.integers()),                          # list (non-str)
    st.dictionaries(st.text(), st.integers()),        # dict (non-str)
    st.tuples(st.integers(), st.text()),              # tuple (non-str)
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 8: Holding_Horizon normalization is total
# ─────────────────────────────────────────────────────────────────────────────


# Feature: earnings-event-risk-gate, Property 8: Holding_Horizon normalization is total
@settings(max_examples=25, deadline=None)
@given(value=_arbitrary_value, config=_config())
def test_property_8_normalization_is_total(value, config):
    """Validates: Requirements 3.2, 4.4

    For ANY value and any configuration, ``normalize_holding_horizon`` never
    raises and always returns a value drawn from ``HOLDING_HORIZONS``; a
    recognized value passes through unchanged (identity) while everything else
    collapses to ``config.default_holding_horizon``.
    """
    result = normalize_holding_horizon(value, config)

    # Totality of the codomain (Requirements 3.2, 4.4): always a recognized horizon.
    assert result in HOLDING_HORIZONS, (
        f"result {result!r} not in HOLDING_HORIZONS for value={value!r}"
    )

    # Matches the independent design rule (identity on recognized, default else).
    expected = _expected_horizon(value, config)
    assert result == expected, (
        f"normalization mismatch for value={value!r} "
        f"default={config.default_holding_horizon!r}: got {result!r}, "
        f"expected {expected!r}"
    )

    # Identity specifically on recognized values (Requirement 4.4).
    if isinstance(value, str) and value in HOLDING_HORIZONS:
        assert result == value, f"recognized value {value!r} not passed through"
    else:
        # Everything else -> the configured default (Requirement 3.2).
        assert result == config.default_holding_horizon


# Feature: earnings-event-risk-gate, Property 8: Holding_Horizon normalization is total
@settings(max_examples=25, deadline=None)
@given(config=_config())
def test_property_8_every_recognized_horizon_is_a_fixed_point(config):
    """Validates: Requirements 4.4

    Every recognized Holding_Horizon is a fixed point of normalization under any
    configuration — normalizing an already-recognized value returns that same
    value unchanged (idempotent identity), independent of the configured
    default.
    """
    for horizon in HOLDING_HORIZONS:
        assert normalize_holding_horizon(horizon, config) == horizon


# Feature: earnings-event-risk-gate, Property 8: Holding_Horizon normalization is total
@settings(max_examples=25, deadline=None)
@given(
    value=st.one_of(
        st.text().filter(lambda s: s not in HOLDING_HORIZONS),
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=True, allow_infinity=True),
    ),
    config=_config(),
)
def test_property_8_unrecognized_collapses_to_default(value, config):
    """Validates: Requirements 3.2

    Any unrecognized value — an unrecognized/empty string, None, a bool, or any
    non-string number (including NaN/inf) — collapses to exactly the configured
    default Holding_Horizon, never to some other value and never raising.
    """
    # Guard: NaN != NaN, but that is irrelevant here since NaN is not a str.
    assert not (isinstance(value, float) and math.isnan(value) and value in HOLDING_HORIZONS)

    result = normalize_holding_horizon(value, config)
    assert result == config.default_holding_horizon
    assert result in HOLDING_HORIZONS
