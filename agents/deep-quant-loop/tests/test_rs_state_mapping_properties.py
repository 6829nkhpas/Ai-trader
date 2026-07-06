"""Property-based test for well-formed states matching the mapping (rs.py, task 3.5).

Feature: relative-strength-context

This module implements design **Property 5: Label states are well-formed and
match the threshold mapping**:

    For any arbitrary ``index_return`` / ``relative_return`` value (finite or
    ``None``) and any resolved ``RSConfig``:

      * ``classify_index_direction`` returns exactly one of ``up`` / ``down`` /
        ``flat``, equal to the value dictated by the design's Index_Direction
        flat-band mapping table (``index_return > +flat_band`` -> ``up``;
        ``index_return < -flat_band`` -> ``down``; otherwise ``flat``), and
      * ``classify_relative_strength_state`` returns exactly one of ``leader`` /
        ``inline`` / ``laggard``, equal to the value dictated by the design's
        Relative_Strength_State leader/laggard-cutoff mapping table
        (``relative_return >= leader_cutoff`` -> ``leader``;
        ``relative_return <= laggard_cutoff`` -> ``laggard``; otherwise
        ``inline``).

Validates: Requirements 1.6, 1.7.

The configuration is drawn across the full threshold space (with the resolver's
``laggard_cutoff < leader_cutoff`` ordering and the documented parameter ranges
enforced) so the mappings are stressed at many threshold positions; the return
values are drawn from ordinary, extreme, threshold-adjacent, and ``None`` pools
so each branch of every mapping is exercised. The sys.path / import pattern
mirrors ``tests/test_rs_clamping_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import (  # noqa: E402
    RSConfig,
    classify_index_direction,
    classify_relative_strength_state,
)

# Expected enumerations (the design's total mapping ranges). Defined locally so
# the test pins the classifiers to the spec rather than to whatever they emit.
_INDEX_DIRECTIONS = {"up", "down", "flat"}
_RELATIVE_STRENGTH_STATES = {"leader", "inline", "laggard"}

# ── Generators ────────────────────────────────────────────────────────────────

# Return values spanning ordinary magnitudes, the extremes of the cutoff domain,
# and ``None`` (the "measure could not be computed" case). The threshold-adjacent
# sampled values stress the boundary comparisons of each mapping branch.
_return_value = st.one_of(
    st.none(),
    st.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    st.sampled_from(
        [0.0, 0.005, -0.005, 0.02, -0.02, 0.5, -0.5, 1.0, -1.0, 1e-9, -1e-9]
    ),
)


@st.composite
def _resolved_config(draw):
    """Draw an ``RSConfig`` honouring the resolver's ranges and cutoff ordering.

    Periods/counts are integers >= 2; the flat band is in ``[0.0, 1.0]``; and the
    cutoffs are in ``[-1.0, 1.0]`` with the strict ``laggard_cutoff <
    leader_cutoff`` ordering the resolver guarantees. Only the threshold fields
    affect the classifiers under test, but every field is populated so the config
    is a faithful, fully-resolved instance.
    """
    lookback = draw(st.integers(min_value=2, max_value=60))
    corr_window = draw(st.integers(min_value=2, max_value=60))
    min_candles = draw(st.integers(min_value=2, max_value=60))

    index_flat_band = draw(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )

    # Enforce the strict laggard < leader ordering the resolver guarantees.
    laggard_cutoff = draw(
        st.floats(min_value=-1.0, max_value=0.99, allow_nan=False, allow_infinity=False)
    )
    leader_cutoff = draw(
        st.floats(
            min_value=laggard_cutoff + 1e-3,
            max_value=1.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )

    return RSConfig(
        lookback=lookback,
        corr_window=corr_window,
        leader_cutoff=leader_cutoff,
        laggard_cutoff=laggard_cutoff,
        index_flat_band=index_flat_band,
        min_candles=min_candles,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: Label states are well-formed and match the threshold mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 5: Label states are well-formed and match the threshold mapping
@settings(max_examples=100, deadline=None)
@given(
    index_return=_return_value,
    relative_return=_return_value,
    rs_ratio_slope=_return_value,
    config=_resolved_config(),
)
def test_property_5_states_well_formed_and_match_mapping(
    index_return, relative_return, rs_ratio_slope, config
):
    """Feature: relative-strength-context, Property 5: Label states are
    well-formed and match the threshold mapping.

    For any arbitrary ``index_return`` / ``relative_return`` (finite or ``None``)
    and any resolved ``RSConfig``:

      * ``classify_index_direction`` is one of up / down / flat and equals the
        flat-band mapping, and
      * ``classify_relative_strength_state`` is one of leader / inline / laggard
        and equals the leader/laggard-cutoff mapping.

    Validates: Requirements 1.6, 1.7
    """
    direction = classify_index_direction(index_return, config)
    state = classify_relative_strength_state(relative_return, rs_ratio_slope, config)

    # Well-formed: each result is drawn from its enumeration (R1.6, R1.7).
    assert direction in _INDEX_DIRECTIONS, (
        f"index_direction {direction!r} not in {_INDEX_DIRECTIONS}"
    )
    assert state in _RELATIVE_STRENGTH_STATES, (
        f"relative_strength_state {state!r} not in {_RELATIVE_STRENGTH_STATES}"
    )

    # Matches the Index_Direction flat-band mapping (R1.6).
    if index_return is None:
        expected_direction = "flat"
    elif index_return > config.index_flat_band:
        expected_direction = "up"
    elif index_return < -config.index_flat_band:
        expected_direction = "down"
    else:
        expected_direction = "flat"
    assert direction == expected_direction, (
        f"index_direction {direction!r} != mapping {expected_direction!r} "
        f"(index_return={index_return!r}, flat_band=+/-{config.index_flat_band})"
    )

    # Matches the Relative_Strength_State leader/laggard-cutoff mapping (R1.7).
    if relative_return is None:
        expected_state = "inline"
    elif relative_return >= config.leader_cutoff:
        expected_state = "leader"
    elif relative_return <= config.laggard_cutoff:
        expected_state = "laggard"
    else:
        expected_state = "inline"
    assert state == expected_state, (
        f"relative_strength_state {state!r} != mapping {expected_state!r} "
        f"(relative_return={relative_return!r}, "
        f"leader>={config.leader_cutoff}, laggard<={config.laggard_cutoff})"
    )
