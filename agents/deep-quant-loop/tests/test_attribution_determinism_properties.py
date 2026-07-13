"""Property-based test for attribution determinism (attribution.py, task 6.2).

Feature: feature-attribution-pruning

This module implements design **Property 1: Determinism**:

    For any list of trade rows and any configuration, building the
    Attribution_Report twice produces deep-equal reports; and for any fixed
    environment, ``resolve_attribution_config`` returns equal configs on repeated
    calls.

Validates: Requirements 8.1, 7.3.

``build_attribution_report`` is a pure function over its in-memory ``rows`` and
the resolved ``AttributionConfig``: it holds no ambient state and never mutates
its inputs, so calling it twice with the SAME rows and SAME config must yield
two structurally identical (deep-equal) reports (Requirement 8.1). Likewise
``resolve_attribution_config`` reads each parameter from its own environment
variable with documented defaults, so under a FIXED environment two consecutive
calls must resolve to equal configurations (Requirement 7.3).

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_recommendation_totality_properties.py``; the
``os.environ`` isolation context mirrors
``tests/test_attribution_config_robustness_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import attribution  # noqa: E402
from attribution import (  # noqa: E402
    AttributionConfig,
    build_attribution_report,
    resolve_attribution_config,
)


# ── Random-but-valid AttributionConfig (covers "any configuration") ───────────
# Each field is drawn within its documented range so the determinism property is
# exercised across the whole configuration space, not just a single fixed config:
#   * min_sample_dimension / min_sample_value / global_min_scored : int >= 1
#   * contribution_threshold : non-negative R-multiple (>= 0.0)
#   * down_weight_factor      : half-open interval (0.0, 1.0]
#   * weight_map_enabled      : bool
@st.composite
def _config(draw):
    """A random AttributionConfig with every field inside its documented range."""
    return AttributionConfig(
        min_sample_dimension=draw(st.integers(min_value=1, max_value=200)),
        min_sample_value=draw(st.integers(min_value=1, max_value=100)),
        contribution_threshold=draw(
            st.floats(
                min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
            )
        ),
        global_min_scored=draw(st.integers(min_value=1, max_value=500)),
        down_weight_factor=draw(
            st.floats(
                min_value=0.0,
                max_value=1.0,
                exclude_min=True,  # (0.0, 1.0]
                allow_nan=False,
                allow_infinity=False,
            )
        ),
        weight_map_enabled=draw(st.booleans()),
    )


# ── Shared journal generators (local to this file) ────────────────────────────
# The real fingerprint dimensions and a small pool of values, so generated keys
# look like the journal's low-cardinality fingerprints and collide across rows
# (exercising real per-value aggregation rather than all-singleton values).
_DIMENSIONS = [
    "dir", "macro", "pred", "va", "regime",
    "rs", "fc", "tm", "sess", "db", "opt",
]
_VALUES = [
    "BUY", "SELL", "aligned", "below", "above",
    "trend-favorable", "leader-aligned", "strong", "weak", "morning",
    "unknown", "",
]

# A finite, usable R-multiple (a *scored* row must carry one of these).
_finite_r = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)

# A non-finite / unusable R-multiple: None, NaN, or ±inf. A win/loss row carrying
# one of these is NOT a Scored_Trade.
_nonfinite_r = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)


@st.composite
def _setup_key(draw):
    """A random ``setup_key``: a structured dimension:value fingerprint, or one of
    a set of malformed / empty keys (robustness coverage)."""
    kind = draw(st.integers(min_value=0, max_value=3))
    if kind == 0:
        # Malformed / empty / degenerate keys the parser must tolerate.
        return draw(st.sampled_from(
            ["", "   ", "|", "||", "a||b", ":", ":trend", "regime", "regime:",
             "regime:unknown", "fc:aligned:strong", "x:|y:unknown|z"]
        ))
    if kind == 1:
        # Wholly arbitrary text.
        return draw(st.text(max_size=40))
    # Structured: a random non-empty subset of dimensions, each with a random
    # value. dict() collapses duplicate dimensions deterministically.
    spec = draw(st.dictionaries(
        keys=st.sampled_from(_DIMENSIONS),
        values=st.sampled_from(_VALUES),
        min_size=1,
        max_size=6,
    ))
    return "|".join(f"{d}:{v}" for d, v in spec.items())


_source = st.sampled_from(["backtest", "live", "LIVE", "Backtest", None, "", "paper"])


@st.composite
def _scored_row(draw):
    """A guaranteed Scored_Trade: win/loss status with a finite ``r_multiple``."""
    return {
        "setup_key": draw(_setup_key()),
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_finite_r),
        "source": draw(_source),
        "symbol": draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None])),
    }


@st.composite
def _non_scored_row(draw):
    """A guaranteed NON-scored row (non-resolving status, or unusable r_multiple)."""
    setup_key = draw(_setup_key())
    source = draw(_source)
    symbol = draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None]))
    if draw(st.booleans()):
        return {
            "setup_key": setup_key,
            "status": draw(st.sampled_from(["open", "expired", "hold", "OPEN", "", "pending"])),
            "r_multiple": draw(st.one_of(_finite_r, _nonfinite_r)),
            "source": source,
            "symbol": symbol,
        }
    return {
        "setup_key": setup_key,
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_nonfinite_r),
        "source": source,
        "symbol": symbol,
    }


@st.composite
def _journal_row(draw):
    """An arbitrary trade row: scored OR non-scored, full range of keys/statuses."""
    if draw(st.booleans()):
        return draw(_scored_row())
    return draw(_non_scored_row())


@st.composite
def _journal(draw, min_size=0, max_size=40):
    """A random journal: a list of arbitrary trade rows."""
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ── os.environ isolation context (fixed environment for the config half) ──────
# Every ATTRIBUTION_* env var the resolver reads. We clear all of them inside the
# isolation context, apply the generated assignment, and restore the prior
# environment exactly on exit so Hypothesis re-runs never leak state. Mirrors
# ``tests/test_attribution_config_robustness_properties.py``.
_ALL_ATTRIBUTION_ENV_VARS = (
    attribution.ENV_MIN_SAMPLE_DIMENSION,
    attribution.ENV_MIN_SAMPLE_VALUE,
    attribution.ENV_CONTRIBUTION_THRESHOLD,
    attribution.ENV_GLOBAL_MIN_SCORED,
    attribution.ENV_DOWN_WEIGHT_FACTOR,
    attribution.ENV_WEIGHT_MAP_ENABLED,
)


@contextmanager
def _attribution_env(overrides):
    """Isolate ``os.environ`` so only ``overrides`` influence the resolver.

    Removes every ATTRIBUTION_* var, applies ``overrides``, and restores the
    prior environment exactly on exit (so Hypothesis re-runs never leak state).
    """
    saved = {name: os.environ.get(name) for name in _ALL_ATTRIBUTION_ENV_VARS}
    try:
        for name in _ALL_ATTRIBUTION_ENV_VARS:
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


# An arbitrary string assigned to each env var (the "fixed environment" under
# which the resolver must be stable across repeated calls). ``None`` leaves the
# var unset; every other branch is a raw string spanning valid, out-of-range,
# blank, and garbage spellings.
_env_token = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    st.text(max_size=10),
    st.sampled_from(
        ["1", "30", "0", "-5", "0.15", "0.5", "1.0", "1.5", "nan", "inf",
         "true", "false", "yes", "no", "on", "off", "abc", " 10 "]
    ),
)

_env_assignment = st.fixed_dictionaries(
    {
        attribution.ENV_MIN_SAMPLE_DIMENSION: _env_token,
        attribution.ENV_MIN_SAMPLE_VALUE: _env_token,
        attribution.ENV_GLOBAL_MIN_SCORED: _env_token,
        attribution.ENV_CONTRIBUTION_THRESHOLD: _env_token,
        attribution.ENV_DOWN_WEIGHT_FACTOR: _env_token,
        attribution.ENV_WEIGHT_MAP_ENABLED: _env_token,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (task 6.2): Determinism
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 1: For any list of trade rows and any configuration, building the Attribution_Report twice produces deep-equal reports; and for any fixed environment, resolve_attribution_config returns equal configs on repeated calls.
@settings(max_examples=100, deadline=None)
@given(rows=_journal(), config=_config())
def test_property_1_report_determinism(rows, config):
    """Feature: feature-attribution-pruning, Property 1: building the
    Attribution_Report twice with the SAME rows and SAME config yields two
    deep-equal reports.

    ``build_attribution_report`` is pure (no ambient state, no input mutation),
    so a second call over identical inputs must reproduce the first report
    exactly — same dimensions in the same order, same per-value stats, same
    contributions, ranks, recommendations, totals, config echo, and flags.

    Validates: Requirements 8.1
    """
    first = build_attribution_report(rows, config)
    second = build_attribution_report(rows, config)

    # Deep structural equality (dict/list ==) — identical reports on repeat.
    assert first == second


# Feature: feature-attribution-pruning, Property 1: For any list of trade rows and any configuration, building the Attribution_Report twice produces deep-equal reports; and for any fixed environment, resolve_attribution_config returns equal configs on repeated calls.
@settings(max_examples=100, deadline=None)
@given(assignment=_env_assignment)
def test_property_1_config_determinism(assignment):
    """Feature: feature-attribution-pruning, Property 1: under a FIXED environment
    ``resolve_attribution_config`` returns equal configs on repeated calls.

    For a single fixed assignment of the ATTRIBUTION_* env vars, two consecutive
    resolutions must produce equal ``AttributionConfig`` values (the resolver
    reads each parameter from its own env var with documented defaults and holds
    no state), so identical environments resolve to identical configuration.

    Validates: Requirements 7.3
    """
    # ``None`` leaves the var UNSET; every other value is assigned as a string.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _attribution_env(overrides):
        first = resolve_attribution_config()
        second = resolve_attribution_config()

    assert isinstance(first, AttributionConfig)
    # Frozen dataclass equality is field-by-field — equal configs on repeat.
    assert first == second
