"""Property-based test for PCR cutoff ordering enforcement (options_bias.py, task 1.3).

Feature: options-agent-integration

This module implements design **Property 18: The resolved PCR cutoffs are always
correctly ordered**:

    For any environment, the configuration resolved by
    ``resolve_options_bias_config`` always satisfies
    ``pcr_bearish_cutoff < pcr_bullish_cutoff``; and whenever the per-parameter
    resolution would produce values that violate that ordering, BOTH PCR cutoffs
    revert to their documented defaults (0.7 and 1.3) together. Resolution never
    raises.

Validates: Requirements 9.3.

Strategy: assign arbitrary env values to ``OPTIONS_BIAS_PCR_BULLISH_CUTOFF`` and
``OPTIONS_BIAS_PCR_BEARISH_CUTOFF`` — spanning unset / empty / whitespace /
garbage / non-finite / out-of-range as well as valid positive floats, with a
dedicated branch that forces inverted/equal pairs (``bearish >= bullish``) so the
ordering guard is exercised directly. The remaining OPTIONS_BIAS_* parameters are
left to arbitrary values to show the PCR-ordering enforcement is independent of
the other parameters.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_of_config_pressure_ordering_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options_bias.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options_bias  # noqa: E402
from options_bias import (  # noqa: E402
    DEFAULT_PCR_BEARISH_CUTOFF,
    DEFAULT_PCR_BULLISH_CUTOFF,
    _PCR_MIN,
    OptionsBiasConfig,
    _resolve_float,
    resolve_options_bias_config,
)

# Every OPTIONS_BIAS_* env var the resolver reads. We clear all of them inside
# the isolation context so only the values under test influence the result and
# the environment never leaks across Hypothesis re-runs.
_ALL_OPTIONS_BIAS_ENV_VARS = (
    options_bias.ENV_PCR_BULLISH_CUTOFF,
    options_bias.ENV_PCR_BEARISH_CUTOFF,
    options_bias.ENV_OI_WALL_PROXIMITY_PCT,
    options_bias.ENV_IV_SKEW_THRESHOLD,
    options_bias.ENV_FUTURES_BASIS_THRESHOLD,
)


@contextmanager
def _options_bias_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every OPTIONS_BIAS_* var, applies ``overrides``, and restores the
    prior environment exactly on exit (so Hypothesis re-runs never leak state).
    Used instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the
    test body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_OPTIONS_BIAS_ENV_VARS}
    try:
        for name in _ALL_OPTIONS_BIAS_ENV_VARS:
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


# An arbitrary env value: ``None`` leaves the var unset; any string spans the
# realistic input space (valid positive, empty, whitespace, non-finite,
# out-of-range, garbage).
_arbitrary_value = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    st.floats(allow_nan=True, allow_infinity=True).map(repr),
    st.integers(min_value=-500, max_value=500).map(str),
    st.floats(min_value=0.0, max_value=10.0).map(repr),
    st.text(max_size=6),
)


@st.composite
def _inverted_or_equal_pcr_pair(draw):
    """Draw an in-range (bullish, bearish) PCR pair with ``bearish >= bullish``.

    Both values are strictly positive and finite so the per-parameter resolution
    keeps them verbatim; constraining ``bearish >= bullish`` guarantees the
    resolved bearish cutoff is *not strictly less than* the resolved bullish
    cutoff — exactly the precondition the ordering guard must catch. Equality is
    allowed so the ``==`` boundary (still "not strictly less than") is exercised.
    """
    bullish = draw(
        st.floats(min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False)
    )
    bearish = draw(
        st.floats(min_value=bullish, max_value=10.0, allow_nan=False, allow_infinity=False)
    )
    return bullish, bearish


# The PCR env values under test: either two fully-arbitrary values, or a
# guaranteed inverted/equal pair (rendered via ``repr``). Mixing both ensures the
# guard is hit directly while still covering the broad input space.
_pcr_env_pair = st.one_of(
    st.tuples(_arbitrary_value, _arbitrary_value),
    _inverted_or_equal_pcr_pair().map(
        lambda pair: (repr(pair[0]), repr(pair[1]))
    ),
)


def _per_parameter_pcr(bullish_raw, bearish_raw):
    """Replicate the per-parameter PCR resolution (before the ordering guard).

    Mirrors the two ``_resolve_float`` calls in ``resolve_options_bias_config``
    so the test can decide, independently, whether the ordering guard *should*
    have fired for the given environment.
    """
    import math

    bullish = _resolve_float(
        options_bias.ENV_PCR_BULLISH_CUTOFF,
        DEFAULT_PCR_BULLISH_CUTOFF,
        _PCR_MIN,
        math.inf,
        low_exclusive=True,
    )
    bearish = _resolve_float(
        options_bias.ENV_PCR_BEARISH_CUTOFF,
        DEFAULT_PCR_BEARISH_CUTOFF,
        _PCR_MIN,
        math.inf,
        low_exclusive=True,
    )
    return bullish, bearish


# ─────────────────────────────────────────────────────────────────────────────
# Property 18 (task 1.3): The resolved PCR cutoffs are always correctly ordered
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 18: The resolved PCR cutoffs are always correctly ordered
@settings(max_examples=200, deadline=None)
@given(
    pcr_pair=_pcr_env_pair,
    oi_wall=_arbitrary_value,
    iv_skew=_arbitrary_value,
    futures_basis=_arbitrary_value,
)
def test_property_18_pcr_cutoffs_always_correctly_ordered(
    pcr_pair, oi_wall, iv_skew, futures_basis
):
    """Feature: options-agent-integration, Property 18: The resolved PCR cutoffs
    are always correctly ordered — for any environment, the resolved config
    satisfies ``pcr_bearish_cutoff < pcr_bullish_cutoff``; and when the
    per-parameter resolution would violate that ordering, BOTH PCR cutoffs revert
    to their documented defaults together. Never raises.

    Validates: Requirements 9.3
    """
    bullish_raw, bearish_raw = pcr_pair

    candidate = {
        options_bias.ENV_PCR_BULLISH_CUTOFF: bullish_raw,
        options_bias.ENV_PCR_BEARISH_CUTOFF: bearish_raw,
        options_bias.ENV_OI_WALL_PROXIMITY_PCT: oi_wall,
        options_bias.ENV_IV_SKEW_THRESHOLD: iv_skew,
        options_bias.ENV_FUTURES_BASIS_THRESHOLD: futures_basis,
    }
    # ``None`` means "leave unset"; everything else is set verbatim.
    overrides = {name: value for name, value in candidate.items() if value is not None}

    with _options_bias_env(overrides):
        # The resolver never raises and produces a fully-formed config.
        config = resolve_options_bias_config()
        # Independently compute the per-parameter resolution inside the same env
        # so we can tell whether the ordering guard *should* have fired.
        per_param_bullish, per_param_bearish = _per_parameter_pcr(
            bullish_raw, bearish_raw
        )

    assert isinstance(config, OptionsBiasConfig)

    # Invariant: the resolved cutoffs are ALWAYS correctly ordered.
    assert config.pcr_bearish_cutoff < config.pcr_bullish_cutoff

    if not (per_param_bearish < per_param_bullish):
        # The ordering guard fired: BOTH cutoffs revert to their documented
        # defaults together — never just one, never the out-of-order values.
        assert config.pcr_bullish_cutoff == DEFAULT_PCR_BULLISH_CUTOFF
        assert config.pcr_bearish_cutoff == DEFAULT_PCR_BEARISH_CUTOFF
    else:
        # The per-parameter values were already correctly ordered, so they pass
        # through unchanged.
        assert config.pcr_bullish_cutoff == per_param_bullish
        assert config.pcr_bearish_cutoff == per_param_bearish

    # The documented defaults themselves satisfy the strict ordering.
    assert DEFAULT_PCR_BEARISH_CUTOFF < DEFAULT_PCR_BULLISH_CUTOFF
