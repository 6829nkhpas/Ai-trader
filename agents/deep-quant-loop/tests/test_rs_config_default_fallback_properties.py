"""Property-based test for per-parameter default fallback (rs.py, task 1.2).

Feature: relative-strength-context

This module implements design **Property 30: Each parameter falls back to its
documented default**:

    When a parameter's environment variable is unset, empty/whitespace-only,
    unparseable as its expected numeric type, or parses but falls outside the
    parameter's valid range, ``resolve_rs_config`` applies that parameter's own
    documented default — independently for every parameter — and never raises.

Validates: Requirements 12.1, 12.2, 12.3, 12.4.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_regime_config_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
from rs import (  # noqa: E402
    DEFAULT_RS_CORR_WINDOW,
    DEFAULT_RS_INDEX_FLAT_BAND,
    DEFAULT_RS_LAGGARD_CUTOFF,
    DEFAULT_RS_LEADER_CUTOFF,
    DEFAULT_RS_LOOKBACK,
    DEFAULT_RS_MIN_CANDLES,
    resolve_rs_config,
)

# Every RS_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_RS_ENV_VARS = (
    rs.ENV_RS_LOOKBACK,
    rs.ENV_RS_CORR_WINDOW,
    rs.ENV_RS_LEADER_CUTOFF,
    rs.ENV_RS_LAGGARD_CUTOFF,
    rs.ENV_RS_INDEX_FLAT_BAND,
    rs.ENV_RS_MIN_CANDLES,
    rs.ENV_RS_DEFAULT_BENCHMARK,
    rs.ENV_RS_BENCHMARK_MAP,
)


@contextmanager
def _rs_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every RS_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_RS_ENV_VARS}
    try:
        for name in _ALL_RS_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


# ── "Bad value" strategies (each should force the documented default) ─────────
# Shared categories that are bad for ANY parameter type: empty, whitespace-only,
# and unparseable non-numeric garbage. ``None`` means "leave the var unset".
_shared_bad = st.one_of(
    st.none(),                                                # unset (R12.2)
    st.just(""),                                              # empty (R12.2)
    st.just("   "),                                           # whitespace-only (R12.2)
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),  # unparseable garbage (R12.3)
)

# Integer params (lookback, corr_window, min_candles) are valid only at >= 2.
# Out-of-range bad values are integers <= 1 (incl. zero/negatives); float-like
# text is unparseable as an int and so also forces the default.
_int_bad = st.one_of(
    _shared_bad,
    st.integers(min_value=-1000, max_value=1).map(str),                 # below min 2 (R12.4)
    st.floats(min_value=2.0, max_value=50.0).map(lambda f: f"{f:.3f}"),  # non-int text (R12.3)
)

# Cutoff params (leader, laggard) are valid only in [-1.0, 1.0]. Out-of-range bad
# values fall strictly outside that band, plus non-finite floats.
_cutoff_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=1.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),   # > 1.0 (R12.4)
    st.floats(min_value=-1e6, max_value=-1.0001, allow_nan=False, allow_infinity=False).map(repr),  # < -1.0 (R12.4)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),                                            # non-finite (R12.3/12.4)
)

# The flat band is valid only in [0.0, 1.0]. Out-of-range bad values are negative
# or above 1.0, plus non-finite floats.
_flat_band_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=1.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),    # > 1.0 (R12.4)
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),  # < 0.0 (R12.4)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),                                            # non-finite (R12.3/12.4)
)

# A complete assignment of a bad value for every parameter at once. Because every
# parameter is bad, every one must fall back to its own documented default; the
# cutoffs revert together so the resolver's default ordering (laggard < leader)
# trivially holds.
_bad_assignment = st.fixed_dictionaries(
    {
        rs.ENV_RS_LOOKBACK: _int_bad,
        rs.ENV_RS_CORR_WINDOW: _int_bad,
        rs.ENV_RS_MIN_CANDLES: _int_bad,
        rs.ENV_RS_LEADER_CUTOFF: _cutoff_bad,
        rs.ENV_RS_LAGGARD_CUTOFF: _cutoff_bad,
        rs.ENV_RS_INDEX_FLAT_BAND: _flat_band_bad,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 30 (task 1.2): Each parameter falls back to its documented default
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 30: Each parameter falls back to its documented default
@settings(max_examples=100, deadline=None)
@given(assignment=_bad_assignment)
def test_property_30_each_parameter_falls_back_to_its_default(assignment):
    """Feature: relative-strength-context, Property 30: Each parameter falls back
    to its documented default — when a parameter's env var is unset, empty/
    whitespace, unparseable as its expected numeric type, or parses but is out of
    range, ``resolve_rs_config`` applies that parameter's documented default and
    never raises.

    Validates: Requirements 12.1, 12.2, 12.3, 12.4
    """
    # Only set the vars the assignment marks as present; ``None`` leaves the var
    # unset so the unset-fallback path (R12.2) is exercised too.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _rs_env(overrides):
        config = resolve_rs_config()

    # The resolver never raised and produced a fully-formed RSConfig.
    assert isinstance(config, rs.RSConfig)

    # Every parameter independently fell back to its own documented default.
    assert config.lookback == DEFAULT_RS_LOOKBACK
    assert config.corr_window == DEFAULT_RS_CORR_WINDOW
    assert config.min_candles == DEFAULT_RS_MIN_CANDLES
    assert config.leader_cutoff == DEFAULT_RS_LEADER_CUTOFF
    assert config.laggard_cutoff == DEFAULT_RS_LAGGARD_CUTOFF
    assert config.index_flat_band == DEFAULT_RS_INDEX_FLAT_BAND
