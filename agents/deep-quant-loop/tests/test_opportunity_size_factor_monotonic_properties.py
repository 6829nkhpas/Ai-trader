"""Property-based test for Size_Factor monotonicity (opportunity.py, task 2.3).

Feature: adaptive-opportunity-engine

This module implements design **Property 2: Size_Factor is monotonic down the
ladder and zero for stand_aside**:

    For any configuration,
        size_factor(a_plus) >= size_factor(b_continuation) >= size_factor(scalp) > 0.0
    and
        size_factor(stand_aside) == 0.0.

Validates: Requirements 1.3.

``size_factor(tier, cfg)`` maps each Opportunity_Tier to its position-size
multiplier: ``a_plus`` -> full, ``b_continuation`` -> reduced, ``scalp`` -> small,
and ``stand_aside`` (plus any unknown tier) -> exactly ``0.0``. The three tradeable
factors are clamped monotonic non-increasing down the ladder inside
``size_factor`` (``b_continuation <= a_plus``, ``scalp <= b_continuation``) so the
ordering holds for *any* configuration — even one whose raw per-tier factors were
not themselves ordered.

The strategy exercises the property along two independent paths so the check is
genuine rather than a tautology:

  * ``configs_direct`` builds ``OpportunityConfig`` instances directly with
    per-tier factors fuzzed across the whole ``(0.0, 1.0]`` interval in EVERY
    order (ascending, descending, equal, and arbitrary), so the internal
    monotonic clamp is stressed regardless of the raw factor order. Because the
    resolver guarantees factors are in ``(0.0, 1.0]``, generating factors in that
    same interval keeps the ``> 0.0`` invariant meaningful for the tradeable
    tiers.
  * ``configs_resolved`` fuzzes the ``OPPORTUNITY_SIZE_FACTOR_*`` environment
    variables and resolves them through ``resolve_opportunity_config`` so the
    property is also validated on configs produced by the real resolution path.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_opportunity_config_resolution_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import opportunity  # noqa: E402
from opportunity import (  # noqa: E402
    DEFAULT_HEARTBEAT_CADENCE_SECS,
    DEFAULT_HEARTBEAT_ENABLED,
    DEFAULT_HEARTBEAT_MAX,
    DEFAULT_LOWER_TIERS_ENABLED,
    DEFAULT_PRUNE_KEEP_RECENT_TURNS,
    DEFAULT_PRUNE_MAX_MESSAGES,
    DEFAULT_SESSION_MAX_TURNS,
    DEFAULT_SESSION_MAX_WALL_SECS,
    DEFAULT_WATCH_CAP,
    OpportunityConfig,
    resolve_opportunity_config,
    size_factor,
)

# Every OPPORTUNITY_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_OPPORTUNITY_ENV_VARS = (
    opportunity.ENV_WATCH_CAP,
    opportunity.ENV_SESSION_MAX_TURNS,
    opportunity.ENV_SESSION_MAX_WALL_SECS,
    opportunity.ENV_SIZE_FACTOR_A_PLUS,
    opportunity.ENV_SIZE_FACTOR_B_CONTINUATION,
    opportunity.ENV_SIZE_FACTOR_SCALP,
    opportunity.ENV_LOWER_TIERS_ENABLED,
    opportunity.ENV_HEARTBEAT_ENABLED,
    opportunity.ENV_HEARTBEAT_CADENCE_SECS,
    opportunity.ENV_HEARTBEAT_MAX,
    opportunity.ENV_PRUNE_KEEP_RECENT_TURNS,
    opportunity.ENV_PRUNE_MAX_MESSAGES,
)


@contextmanager
def _opportunity_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every OPPORTUNITY_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state).
    """
    saved = {name: os.environ.get(name) for name in _ALL_OPPORTUNITY_ENV_VARS}
    try:
        for name in _ALL_OPPORTUNITY_ENV_VARS:
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


# ── Config-building strategies ────────────────────────────────────────────────
# A per-tier Size_Factor in the half-open interval (0.0, 1.0] — the interval the
# resolver guarantees, so the "> 0.0" invariant for the tradeable tiers is
# meaningful. ``exclude_min`` keeps the value strictly positive.
_factor = st.floats(
    min_value=0.0, max_value=1.0, exclude_min=True, allow_nan=False, allow_infinity=False
)


@st.composite
def configs_direct(draw):
    """Build an ``OpportunityConfig`` directly with fuzzed in-range Size_Factors.

    The three per-tier factors are drawn INDEPENDENTLY across (0.0, 1.0] so their
    raw order is arbitrary (ascending, descending, equal, or mixed), stressing the
    monotonic clamp inside ``size_factor``. The non-factor fields are held at their
    documented defaults — they do not influence ``size_factor``.
    """
    return OpportunityConfig(
        watch_cap=DEFAULT_WATCH_CAP,
        session_max_turns=DEFAULT_SESSION_MAX_TURNS,
        session_max_wall_secs=DEFAULT_SESSION_MAX_WALL_SECS,
        size_factor_a_plus=draw(_factor),
        size_factor_b_continuation=draw(_factor),
        size_factor_scalp=draw(_factor),
        lower_tiers_enabled=DEFAULT_LOWER_TIERS_ENABLED,
        heartbeat_enabled=DEFAULT_HEARTBEAT_ENABLED,
        heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
        heartbeat_max=DEFAULT_HEARTBEAT_MAX,
        prune_keep_recent_turns=DEFAULT_PRUNE_KEEP_RECENT_TURNS,
        prune_max_messages=DEFAULT_PRUNE_MAX_MESSAGES,
    )


def _assert_size_factor_property(cfg):
    """Assert Property 2 holds for a single configuration."""
    a_plus = size_factor("a_plus", cfg)
    b_cont = size_factor("b_continuation", cfg)
    scalp = size_factor("scalp", cfg)
    aside = size_factor("stand_aside", cfg)

    # Monotonic non-increasing down the ladder, all tradeable tiers strictly > 0.
    assert a_plus >= b_cont >= scalp > 0.0, (
        f"expected a_plus({a_plus}) >= b_continuation({b_cont}) >= "
        f"scalp({scalp}) > 0.0"
    )
    # stand_aside is exactly zero.
    assert aside == 0.0, f"expected size_factor(stand_aside) == 0.0, got {aside}"

    # Every tradeable factor stays within the documented (0.0, 1.0] band.
    for name, value in (("a_plus", a_plus), ("b_continuation", b_cont), ("scalp", scalp)):
        assert 0.0 < value <= 1.0, f"{name} factor {value} out of (0.0, 1.0]"

    # An unknown tier is total and yields exactly 0.0 (never raises).
    assert size_factor("unknown", cfg) == 0.0
    assert size_factor("", cfg) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (task 2.3): Size_Factor is monotonic down the ladder and zero for
# stand_aside — validated on directly-constructed configs.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 2: For any configuration, size_factor(a_plus) >= size_factor(b_continuation) >= size_factor(scalp) > 0.0 and size_factor(stand_aside) == 0.0.
@settings(max_examples=200, deadline=None)
@given(cfg=configs_direct())
def test_property_2_size_factor_monotonic_direct(cfg):
    """Feature: adaptive-opportunity-engine, Property 2: Size_Factor is monotonic
    down the ladder and zero for stand_aside — for any ``OpportunityConfig`` built
    with per-tier factors fuzzed across (0.0, 1.0] in arbitrary order,
    ``size_factor`` returns a_plus >= b_continuation >= scalp > 0.0 and
    stand_aside == 0.0.

    Validates: Requirements 1.3
    """
    _assert_size_factor_property(cfg)


# ── Env-fuzzing strategy for the resolved path ────────────────────────────────
_factor_token = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    st.floats(allow_nan=True, allow_infinity=True).map(repr),
    st.floats(min_value=-2.0, max_value=2.0).map(lambda f: f"{f:.4f}"),
    st.sampled_from(["0.3", "0.0", "-0.1", "0.6", "1.0", "1.5", "abc", " 0.5 "]),
    st.text(max_size=8),
)

_factor_assignment = st.fixed_dictionaries(
    {
        opportunity.ENV_SIZE_FACTOR_A_PLUS: _factor_token,
        opportunity.ENV_SIZE_FACTOR_B_CONTINUATION: _factor_token,
        opportunity.ENV_SIZE_FACTOR_SCALP: _factor_token,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (task 2.3): validated on configs produced by the real resolution
# path (env fuzzing -> resolve_opportunity_config), which clamps every factor into
# (0.0, 1.0] before size_factor further clamps them monotonic.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 2: For any configuration (including ones resolved from arbitrary environment values), size_factor(a_plus) >= size_factor(b_continuation) >= size_factor(scalp) > 0.0 and size_factor(stand_aside) == 0.0.
@settings(max_examples=200, deadline=None)
@given(assignment=_factor_assignment)
def test_property_2_size_factor_monotonic_resolved(assignment):
    """Feature: adaptive-opportunity-engine, Property 2: Size_Factor is monotonic
    down the ladder and zero for stand_aside — holds for configs resolved from
    arbitrary (unset/empty/unparseable/out-of-range) Size_Factor env values.

    Validates: Requirements 1.3
    """
    overrides = {name: value for name, value in assignment.items() if value is not None}
    with _opportunity_env(overrides):
        cfg = resolve_opportunity_config()
    _assert_size_factor_property(cfg)
