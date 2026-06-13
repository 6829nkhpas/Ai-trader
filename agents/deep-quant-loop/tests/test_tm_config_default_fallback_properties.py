"""Property-based test for per-parameter default fallback (trade_manager.py, task 1.2).

Feature: trade-management

This module implements design **Property 26: Configuration default fallback**:

    When a parameter's environment variable is unset, empty/whitespace-only,
    unparseable as its expected numeric type, or parses but falls outside the
    parameter's valid range, ``resolve_trade_manager_config`` applies that
    parameter's own documented default — independently for every parameter — and
    never raises.

Validates: Requirements 13.2, 13.3, 13.4.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_rs_config_default_fallback_properties.py`` and
``tests/test_of_config_default_fallback_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (trade_manager.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import trade_manager  # noqa: E402
from trade_manager import (  # noqa: E402
    DEFAULT_BREAKEVEN_TRIGGER_R,
    DEFAULT_FIRST_TARGET_FRACTION,
    DEFAULT_FIRST_TARGET_R,
    DEFAULT_MIN_BLENDED_REWARD_TO_RISK,
    DEFAULT_TRAIL_ATR_MULTIPLE,
    resolve_trade_manager_config,
)

# Every TM_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_TM_ENV_VARS = (
    trade_manager.ENV_DEFAULT_FIRST_TARGET_R,
    trade_manager.ENV_DEFAULT_FIRST_TARGET_FRACTION,
    trade_manager.ENV_DEFAULT_BREAKEVEN_TRIGGER_R,
    trade_manager.ENV_DEFAULT_TRAIL_ATR_MULTIPLE,
    trade_manager.ENV_MIN_BLENDED_REWARD_TO_RISK,
)


@contextmanager
def _tm_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every TM_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_TM_ENV_VARS}
    try:
        for name in _ALL_TM_ENV_VARS:
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
    st.none(),                                                # unset (R13.2)
    st.just(""),                                              # empty (R13.2)
    st.just("   "),                                           # whitespace-only (R13.2)
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),  # unparseable garbage (R13.3)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),      # non-finite (R13.3/13.4)
)

# R-multiple params (first_target_r, breakeven_trigger_r): valid range (0, 100].
# Out-of-range bad values are <= 0.0 (incl. the exclusive 0.0 boundary and
# negatives) or strictly above 100.0.
_r_multiple_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=-1e6, max_value=0.0, allow_nan=False, allow_infinity=False).map(repr),       # <= 0.0 (R13.4)
    st.floats(min_value=100.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),   # > 100 (R13.4)
)

# Fraction param (first_target_fraction): valid range (0, 1]. Out-of-range bad
# values are <= 0.0 (incl. the exclusive 0.0 boundary and negatives) or > 1.0.
_fraction_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=-1e6, max_value=0.0, allow_nan=False, allow_infinity=False).map(repr),       # <= 0.0 (R13.4)
    st.floats(min_value=1.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),     # > 1.0 (R13.4)
)

# Inclusive-lower params (trail_atr_multiple, min_blended_reward_to_risk): valid
# range [0, 100], so 0.0 is VALID and must NOT appear as a bad value. Out-of-range
# bad values are strictly negative or strictly above 100.0.
_inclusive_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),   # < 0.0 (R13.4)
    st.floats(min_value=100.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),   # > 100 (R13.4)
)

# A complete assignment of a bad value for every parameter at once. Because every
# parameter is bad, every one must independently fall back to its own documented
# default.
_bad_assignment = st.fixed_dictionaries(
    {
        trade_manager.ENV_DEFAULT_FIRST_TARGET_R: _r_multiple_bad,
        trade_manager.ENV_DEFAULT_FIRST_TARGET_FRACTION: _fraction_bad,
        trade_manager.ENV_DEFAULT_BREAKEVEN_TRIGGER_R: _r_multiple_bad,
        trade_manager.ENV_DEFAULT_TRAIL_ATR_MULTIPLE: _inclusive_bad,
        trade_manager.ENV_MIN_BLENDED_REWARD_TO_RISK: _inclusive_bad,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 26 (task 1.2): Configuration default fallback
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 26: Configuration default fallback
@settings(max_examples=200, deadline=None)
@given(assignment=_bad_assignment)
def test_property_26_each_parameter_falls_back_to_its_default(assignment):
    """Feature: trade-management, Property 26: Configuration default fallback —
    when a parameter's env var is unset, empty/whitespace, unparseable as its
    expected numeric type, or parses but is out of range,
    ``resolve_trade_manager_config`` applies that parameter's documented default
    and never raises.

    Validates: Requirements 13.2, 13.3, 13.4
    """
    # Only set the vars the assignment marks as present; ``None`` leaves the var
    # unset so the unset-fallback path (R13.2) is exercised too.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _tm_env(overrides):
        config = resolve_trade_manager_config()

    # The resolver never raised and produced a fully-formed TradeManagerConfig.
    assert isinstance(config, trade_manager.TradeManagerConfig)

    # Every parameter independently fell back to its own documented default.
    assert config.default_first_target_r == DEFAULT_FIRST_TARGET_R
    assert config.default_first_target_fraction == DEFAULT_FIRST_TARGET_FRACTION
    assert config.default_breakeven_trigger_r == DEFAULT_BREAKEVEN_TRIGGER_R
    assert config.default_trail_atr_multiple == DEFAULT_TRAIL_ATR_MULTIPLE
    assert config.min_blended_reward_to_risk == DEFAULT_MIN_BLENDED_REWARD_TO_RISK
