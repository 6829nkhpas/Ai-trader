"""Property-based test for relative-strength parameter resolution (rs.py, task 1.4).

Feature: relative-strength-context

This module implements design **Property 32: Parameter resolution is
deterministic and path-independent**:

    For any environment, ``resolve_rs_config`` returns equal ``RSConfig`` values
    across repeated calls and across the Relative_Strength_Tool path and the
    Backtest_Seeder path, so identical environment values resolve to identical
    parameters on both paths.

Validates: Requirements 12.6.

The strategy below assigns each RS_* environment variable an arbitrary value
drawn from the realistic input space the resolver must tolerate (unset, empty,
whitespace-only, valid numeric, out-of-range, NaN/inf, and arbitrary garbage),
then asserts that ``resolve_rs_config`` returns equal ``RSConfig`` instances
across repeated and independent (interleaved, reordered) call sites — modelling
the live tool path and the backtest path resolving from the identical
environment.

The sys.path / import pattern mirrors ``tests/test_regime_config_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``rs`` is
importable when pytest is run from anywhere.
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
from rs import resolve_rs_config  # noqa: E402

# Every RS_* env var the resolver reads. We clear all of them inside the
# isolation context so only the assignment under test influences the result and
# any unset parameter falls back to its documented default deterministically.
_ALL_RS_ENV_VARS = (
    rs.ENV_RS_LOOKBACK,
    rs.ENV_RS_CORR_WINDOW,
    rs.ENV_RS_LEADER_CUTOFF,
    rs.ENV_RS_LAGGARD_CUTOFF,
    rs.ENV_RS_INDEX_FLAT_BAND,
    rs.ENV_RS_MIN_CANDLES,
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


# A single env-var value: ``None`` means "leave the variable unset"; any string
# (valid numeric, out-of-range, empty/whitespace, NaN/inf, or unparseable
# garbage) is set verbatim. This spans the realistic input space the resolver
# must tolerate.
_env_value = st.one_of(
    st.none(),                                                 # unset
    st.just(""),                                               # empty
    st.just("   "),                                            # whitespace-only
    st.floats(allow_nan=True, allow_infinity=True).map(repr),  # numeric (+ nan/inf)
    st.integers(min_value=-500, max_value=500).map(str),       # integer-like
    st.text(max_size=8),                                       # arbitrary garbage
)

# A complete assignment over every RS env var at once.
_env_assignments = st.fixed_dictionaries(
    {name: _env_value for name in _ALL_RS_ENV_VARS}
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 32 (1.4): Parameter resolution is deterministic and path-independent
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 32: Parameter resolution is deterministic and path-independent
@settings(max_examples=100, deadline=None)
@given(assignment=_env_assignments)
def test_property_32_rs_resolution_is_deterministic_and_path_independent(assignment):
    """Feature: relative-strength-context, Property 32: Parameter resolution is
    deterministic and path-independent — for identical environment-variable
    values, ``resolve_rs_config`` returns equal ``RSConfig`` instances across
    repeated and independent calls (e.g. the Relative_Strength_Tool path vs the
    Backtest_Seeder path), regardless of call order.

    Validates: Requirements 12.6
    """
    # Only set the vars the assignment marks as present; the rest stay unset.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _rs_env(overrides):
        # "Tool path" and "backtest path" are independent call sites of the same
        # resolver. Call them repeatedly and interleaved to expose any hidden
        # ordering- or call-count-dependence.
        tool_path_first = resolve_rs_config()
        backtest_path_first = resolve_rs_config()
        tool_path_second = resolve_rs_config()
        backtest_path_second = resolve_rs_config()

        # Every resolved configuration is an RSConfig (the resolver never raised).
        for config in (
            tool_path_first,
            backtest_path_first,
            tool_path_second,
            backtest_path_second,
        ):
            assert isinstance(config, rs.RSConfig)

        # Identical env => identical config across all calls and both paths.
        assert tool_path_first == backtest_path_first
        assert tool_path_first == tool_path_second
        assert tool_path_first == backtest_path_second
        assert backtest_path_first == backtest_path_second

        # Order-independence: resolving again in a different call order yields the
        # same (identical) result every time.
        reordered = [resolve_rs_config() for _ in range(5)]
        assert all(config == tool_path_first for config in reordered)
