# Feature: earnings-event-risk-gate, Property 5: Event_Risk is a total, exhaustive function of days-until-event and Holding_Horizon
"""Property-based test for Event_Risk totality and mapping (events.py, task 2.7).

Feature: earnings-event-risk-gate

This module implements design **Property 5: Event_Risk is a total, exhaustive
function of days-until-event and Holding_Horizon**:

    For every non-negative day count and every recognized Holding_Horizon (over
    an arbitrary configuration whose windows satisfy ``through_event_window_days
    <= imminent_window_days``), ``classify_event_risk`` returns exactly one of
    ``clear`` / ``imminent`` / ``through_event`` and equals the value dictated by
    the design mapping table:

      * ``intraday``      : ``d == 0`` -> ``through_event``; ``d >= 1`` -> ``clear``
      * ``multi_session`` : ``d <= T`` -> ``through_event``;
                            ``T < d <= I`` -> ``imminent``; ``d > I`` -> ``clear``

    where ``T = through_event_window_days`` and ``I = imminent_window_days``.

Validates: Requirements 2.3, 2.4, 2.5, 2.6.

The classifier is *total* (Requirement 2.3): every ``(days >= 0, horizon)``
combination maps to exactly one Event_Risk value. To make the totality and the
exhaustive mapping concrete, the expected value is computed by an INDEPENDENT
reference implementation (``_expected_event_risk``) that re-derives the design
mapping table here rather than calling the implementation's helper — that is
what makes this a real check that the implementation matches the specified
mapping.

Both boundary-aware day counts (values exactly at ``0`` / ``T`` / ``I`` and one
step either side) and arbitrary large day counts are generated, and both
recognized Holding_Horizons are covered, so every branch and every precedence
edge of the mapping is exercised.

The sys.path / import pattern mirrors the sibling ``test_event_*`` and
``test_session_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from events import (  # noqa: E402
    EVENT_RISK_CLEAR,
    EVENT_RISK_IMMINENT,
    EVENT_RISK_THROUGH_EVENT,
    EventConfig,
    classify_event_risk,
)

# The complete, fixed three-value Event_Risk enumeration (Requirement 2.3).
THREE_RISKS = {EVENT_RISK_CLEAR, EVENT_RISK_IMMINENT, EVENT_RISK_THROUGH_EVENT}

# The two recognized Holding_Horizons the mapping is defined over.
_HORIZONS = ["intraday", "multi_session"]


# ─────────────────────────────────────────────────────────────────────────────
# Independent reference implementation of the design's Event_Risk mapping table.
# ─────────────────────────────────────────────────────────────────────────────


def _expected_event_risk(days: int, horizon: str, through: int, imminent: int) -> str:
    """Re-derive the Event_Risk from the design mapping table (Requirements
    2.4-2.6), assuming ``days >= 0``, ``horizon`` recognized, and
    ``through <= imminent``:

        intraday      : d == 0        -> through_event   (event lands intraday)
                        d >= 1        -> clear           (future-dated, R2.4)
        multi_session : d <= through  -> through_event   (R2.5)
                        d <= imminent  -> imminent        (R2.6, given d > through)
                        else          -> clear
    """
    if horizon == "intraday":
        return EVENT_RISK_THROUGH_EVENT if days == 0 else EVENT_RISK_CLEAR
    # multi_session
    if days <= through:
        return EVENT_RISK_THROUGH_EVENT
    if days <= imminent:
        return EVENT_RISK_IMMINENT
    return EVENT_RISK_CLEAR


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────


@st.composite
def _config(draw):
    """An arbitrary ``EventConfig`` whose windows satisfy the ordering invariant
    ``through_event_window_days <= imminent_window_days`` that
    ``resolve_event_config`` guarantees. Non-window fields are irrelevant to
    ``classify_event_risk`` and take arbitrary-but-valid values.
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


@st.composite
def _days(draw, config: EventConfig):
    """A non-negative day count, biased to the mapping's boundary edges (``0``,
    ``T``, ``I``, and one step either side) so every precedence edge is hit,
    plus arbitrary small and large day counts."""
    through = config.through_event_window_days
    imminent = config.imminent_window_days
    edges = []
    for base in (0, through, imminent):
        for delta in (-1, 0, 1):
            v = base + delta
            if v >= 0:
                edges.append(v)
    return draw(
        st.one_of(
            st.sampled_from(edges),
            st.integers(min_value=0, max_value=10),
            st.integers(min_value=0, max_value=100_000),
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: Event_Risk is a total, exhaustive function of days & Holding_Horizon
# ─────────────────────────────────────────────────────────────────────────────


# Feature: earnings-event-risk-gate, Property 5: Event_Risk is a total, exhaustive function of days-until-event and Holding_Horizon
@settings(max_examples=300, deadline=None)
@given(data=st.data(), config=_config(), horizon=st.sampled_from(_HORIZONS))
def test_property_5_event_risk_totality_and_mapping(data, config, horizon):
    """Validates: Requirements 2.3, 2.4, 2.5, 2.6

    For every non-negative day count and every recognized Holding_Horizon over an
    arbitrary ordering-consistent configuration, ``classify_event_risk`` returns
    exactly one of the three Event_Risk values (totality / exhaustiveness) and
    equals the value dictated by the design mapping table (an independent
    re-derivation of Requirements 2.4-2.6).
    """
    days = data.draw(_days(config))

    risk = classify_event_risk(days, horizon, config)

    # Totality / exhaustiveness (Requirement 2.3): exactly one of three values.
    assert risk in THREE_RISKS, f"risk {risk!r} not in the three-value enum"

    # Matches the exhaustive design mapping table (Requirements 2.4-2.6).
    expected = _expected_event_risk(
        days, horizon, config.through_event_window_days, config.imminent_window_days
    )
    assert risk == expected, (
        f"risk mismatch for days={days} horizon={horizon} "
        f"through={config.through_event_window_days} "
        f"imminent={config.imminent_window_days}: got {risk!r}, expected {expected!r}"
    )

    # Boundary specifics called out by the acceptance criteria.
    if horizon == "intraday" and days >= 1:
        # A same-session trade does not straddle a future-dated event (R2.4).
        assert risk == EVENT_RISK_CLEAR
    if horizon == "multi_session" and days <= config.through_event_window_days:
        # Within the through-event window -> held through the event (R2.5).
        assert risk == EVENT_RISK_THROUGH_EVENT


# Feature: earnings-event-risk-gate, Property 5: Event_Risk is a total, exhaustive function of days-until-event and Holding_Horizon
@settings(max_examples=200, deadline=None)
@given(
    data=st.data(),
    config=_config(),
)
def test_property_5_deterministic_single_valued_over_both_horizons(data, config):
    """Validates: Requirements 2.3

    For a single day count, classifying under BOTH horizons yields, for each, a
    single well-formed Event_Risk value — demonstrating the function is total
    (defined) and single-valued for every ``(days, horizon)`` cell of the mapping
    grid, with no combination left undefined or ambiguous.
    """
    days = data.draw(_days(config))

    results = {h: classify_event_risk(days, h, config) for h in _HORIZONS}

    # Every cell is defined and single-valued in the three-value enum.
    for horizon, risk in results.items():
        assert risk in THREE_RISKS, (
            f"undefined/ill-formed risk for days={days} horizon={horizon}: {risk!r}"
        )
        assert risk == _expected_event_risk(
            days,
            horizon,
            config.through_event_window_days,
            config.imminent_window_days,
        )
