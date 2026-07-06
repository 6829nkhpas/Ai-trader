"""Property-based test for config fallback to documented defaults (options_bias.py, task 1.2).

Feature: options-agent-integration

This module implements design **Property 17: Config resolution falls back to
documented defaults and never raises**:

    For ANY combination of the five ``OPTIONS_BIAS_*`` environment variables —
    unset, empty/whitespace-only, unparseable as a float, non-finite (NaN/inf),
    out of the parameter's valid range, or a valid in-range value —
    ``resolve_options_bias_config()`` never raises and always returns an
    ``OptionsBiasConfig`` whose fields equal the documented defaults whenever the
    env value is unset/empty/unparseable/non-finite/out-of-range, and the parsed
    value whenever it is valid and in range.

Validates: Requirements 9.1, 9.2.

Each parameter resolves independently from its own env var (Requirement 9.1) and
falls back to its documented default without raising on any bad value
(Requirement 9.2). The only coupling is the PCR ordering guard (Requirement
9.3): when ``pcr_bearish_cutoff < pcr_bullish_cutoff`` does not hold after the
per-parameter resolution, BOTH PCR cutoffs revert to their documented defaults
together — this test models that guard when computing the expected PCR values.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_of_config_default_fallback_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the agent package importable (options_bias.py lives one level up),
# mirroring the sibling property-test modules in this directory.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options_bias  # noqa: E402
from options_bias import (  # noqa: E402
    DEFAULT_FUTURES_BASIS_THRESHOLD,
    DEFAULT_IV_SKEW_THRESHOLD,
    DEFAULT_OI_WALL_PROXIMITY_PCT,
    DEFAULT_PCR_BEARISH_CUTOFF,
    DEFAULT_PCR_BULLISH_CUTOFF,
    OptionsBiasConfig,
    resolve_options_bias_config,
)

# Every OPTIONS_BIAS_* env var the resolver reads. We clear all of them inside
# the isolation context so only the values under test influence the result and
# the environment never leaks across Hypothesis re-runs.
_ALL_ENV_VARS = (
    options_bias.ENV_PCR_BULLISH_CUTOFF,
    options_bias.ENV_PCR_BEARISH_CUTOFF,
    options_bias.ENV_OI_WALL_PROXIMITY_PCT,
    options_bias.ENV_IV_SKEW_THRESHOLD,
    options_bias.ENV_FUTURES_BASIS_THRESHOLD,
)


@contextmanager
def _bias_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every OPTIONS_BIAS_* var, applies ``overrides``, and restores the
    prior environment exactly on exit (so Hypothesis re-runs never leak state).
    Used instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the
    test body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_ENV_VARS}
    try:
        for name in _ALL_ENV_VARS:
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


# ── Shared "bad value" raws (force the documented default for ANY parameter) ──
# ``None`` means "leave the var unset" (R9.2). Empty / whitespace-only strip to
# nothing; the garbage alphabet has no digits so can never parse as a float.
_shared_bad_raw = st.one_of(
    st.none(),                                                # unset (R9.2)
    st.just(""),                                              # empty (R9.2)
    st.just("   "),                                           # whitespace-only (R9.2)
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),  # unparseable garbage
)

# Non-finite literals parse to a float but are rejected (R9.2).
_nonfinite_raw = st.sampled_from(["nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"])


def _valid_pair(valid_floats):
    """Map an in-range float strategy to a ``(raw, expected)`` pair.

    ``repr`` round-trips exactly for Python floats, so the resolver parses back
    the identical value: ``float(repr(v)) == v``.
    """
    return valid_floats.map(lambda v: (repr(v), v))


def _bad_pair(extra_raw, default):
    """Map a bad-value raw (shared or parameter-specific) to ``(raw, default)``."""
    return st.one_of(_shared_bad_raw, extra_raw).map(lambda r: (r, default))


# ── PCR cutoffs: valid range is (0.0, inf) — strictly positive, unbounded ─────
_pcr_valid = _valid_pair(
    st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False)
)
_pcr_bad_extra = st.one_of(
    # <= 0.0 is out of the exclusive lower bound (includes exactly 0.0).
    st.floats(min_value=-1e6, max_value=0.0, allow_nan=False, allow_infinity=False).map(repr),
    _nonfinite_raw,
)


def _pcr_field(default):
    return st.one_of(_pcr_valid, _bad_pair(_pcr_bad_extra, default))


# ── OI-wall proximity: valid range is [0.0, 1.0] (a decimal fraction) ─────────
_oiw_valid = _valid_pair(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
)
_oiw_bad_extra = st.one_of(
    st.floats(min_value=1.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),
    _nonfinite_raw,
)
_oiw_field = st.one_of(_oiw_valid, _bad_pair(_oiw_bad_extra, DEFAULT_OI_WALL_PROXIMITY_PCT))


# ── IV-skew / futures-basis thresholds: valid range is [0.0, inf) ─────────────
_thr_valid = _valid_pair(
    st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
)
_thr_bad_extra = st.one_of(
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),
    _nonfinite_raw,
)


def _thr_field(default):
    return st.one_of(_thr_valid, _bad_pair(_thr_bad_extra, default))


# A complete assignment: each env var -> a ``(raw, expected)`` pair. ``raw`` is
# either a string to set or ``None`` to leave the var unset; ``expected`` is the
# per-parameter resolved value BEFORE the PCR ordering guard is applied.
_assignment = st.fixed_dictionaries(
    {
        options_bias.ENV_PCR_BULLISH_CUTOFF: _pcr_field(DEFAULT_PCR_BULLISH_CUTOFF),
        options_bias.ENV_PCR_BEARISH_CUTOFF: _pcr_field(DEFAULT_PCR_BEARISH_CUTOFF),
        options_bias.ENV_OI_WALL_PROXIMITY_PCT: _oiw_field,
        options_bias.ENV_IV_SKEW_THRESHOLD: _thr_field(DEFAULT_IV_SKEW_THRESHOLD),
        options_bias.ENV_FUTURES_BASIS_THRESHOLD: _thr_field(DEFAULT_FUTURES_BASIS_THRESHOLD),
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 17 (task 1.2): Config resolution falls back to documented defaults
# and never raises
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 17: Config resolution falls back to documented defaults and never raises
@settings(max_examples=300, deadline=None)
@given(assignment=_assignment)
def test_property_17_config_falls_back_to_documented_defaults(assignment):
    """Validates: Requirements 9.1, 9.2

    For arbitrary env-var values (unset / empty / garbage / non-finite /
    out-of-range / valid), ``resolve_options_bias_config()`` never raises and
    returns an ``OptionsBiasConfig`` whose every field equals the documented
    default when the value is unusable, and the parsed value when valid and in
    range. The only coupling is the PCR ordering guard (R9.3): when the resolved
    bearish cutoff is not strictly below the resolved bullish cutoff, both PCR
    cutoffs revert to their documented defaults together.
    """
    # Only set the vars whose raw is present; ``None`` exercises the unset path.
    overrides = {
        name: raw for name, (raw, _expected) in assignment.items() if raw is not None
    }

    with _bias_env(overrides):
        config = resolve_options_bias_config()

    # The resolver never raised and produced a fully-formed OptionsBiasConfig.
    assert isinstance(config, OptionsBiasConfig)

    # Per-parameter expected values (before the PCR ordering guard).
    exp_pcr_bull = assignment[options_bias.ENV_PCR_BULLISH_CUTOFF][1]
    exp_pcr_bear = assignment[options_bias.ENV_PCR_BEARISH_CUTOFF][1]
    exp_oiw = assignment[options_bias.ENV_OI_WALL_PROXIMITY_PCT][1]
    exp_iv = assignment[options_bias.ENV_IV_SKEW_THRESHOLD][1]
    exp_fb = assignment[options_bias.ENV_FUTURES_BASIS_THRESHOLD][1]

    # PCR ordering guard (R9.3): both cutoffs revert to defaults together when
    # the bearish cutoff is not strictly below the bullish cutoff.
    if not (exp_pcr_bear < exp_pcr_bull):
        exp_pcr_bull = DEFAULT_PCR_BULLISH_CUTOFF
        exp_pcr_bear = DEFAULT_PCR_BEARISH_CUTOFF

    # The independent, in-range parameters always equal their per-parameter
    # expected value (parsed value when valid, documented default otherwise).
    assert config.oi_wall_proximity_pct == exp_oiw
    assert config.iv_skew_threshold == exp_iv
    assert config.futures_basis_threshold == exp_fb
    assert config.pcr_bullish_cutoff == exp_pcr_bull
    assert config.pcr_bearish_cutoff == exp_pcr_bear

    # The resolved PCR ordering invariant always holds.
    assert config.pcr_bearish_cutoff < config.pcr_bullish_cutoff
