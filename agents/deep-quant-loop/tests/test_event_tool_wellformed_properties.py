# Feature: earnings-event-risk-gate, Property 12: A successful tool result is well-formed
"""Property-based test for a well-formed successful tool result (tools.py, task 4.5).

Feature: earnings-event-risk-gate

This Hypothesis property exercises the ``get_event_risk`` tool in ``tools.py``
with BOTH the Event_Source reader AND the process clock MOCKED. It covers design
Property 12: when the source yields an upcoming event and the gate is enabled, a
SUCCESSFUL (non-unavailable, non-error) ``get_event_risk`` result is a
well-formed Event_Assessment — ``days_until_event`` is a finite non-negative
number or ``null``, ``event_risk`` is drawn from the three-value
``EVENT_RISK_STATES`` enum, ``event_recommendation`` is drawn from the
four-value ``EVENT_RECOMMENDATIONS`` enum, and ``event_date`` is a string.

The tool reads the reference "now" via ``time.time()`` and gathers candidate
event dates via ``_load_event_candidates``. Here ``tools.time.time`` is patched
to a generated fixed value and ``tools._load_event_candidates`` is patched to
return a structured result carrying a strictly-future epoch-ms candidate, so the
test exercises the full tool path (gate check -> arg validation -> config
resolution -> horizon normalization -> nearest-future selection -> classify ->
contract re-validation) with NO network / filesystem I/O.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_session_tool_wellformed_properties.py``.

Validates: Requirements 4.5
"""

import math
import os
import sys
from unittest import mock

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / events.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import (  # noqa: E402
    EVENT_RISK_STATES,
    EVENT_RECOMMENDATIONS,
    get_event_risk,
)

# A non-empty symbol so argument validation passes.
_SYMBOL = "RELIANCE"

# Epoch-millisecond bounds for the reference "now" spanning many years / weekdays
# / times-of-day so the generated reference lands on / off event weekdays across
# the whole space. 2015-01-01 .. 2035-01-01 (UTC), in milliseconds.
_NOW_MIN_MS = 1_420_070_400_000
_NOW_MAX_MS = 2_051_222_400_000

# One day / one hour in milliseconds.
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000

# Both recognized horizons plus absent / unrecognized values (which normalize to
# the documented default) so every Holding_Horizon path is exercised.
_HORIZONS = st.sampled_from(["intraday", "multi_session", "", "   ", "swing", "n/a"])


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _is_finite_nonneg_or_null(value) -> bool:
    """True when ``value`` is None or a finite, non-negative real number.

    Bools are excluded (they are not a numeric days-until-event).
    """
    if value is None:
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 12: A successful tool result is well-formed
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 12: A successful tool result is well-formed
@settings(max_examples=25, deadline=None)
@given(
    now_ms=st.integers(min_value=_NOW_MIN_MS, max_value=_NOW_MAX_MS),
    future_days=st.integers(min_value=0, max_value=400),
    holding_horizon=_HORIZONS,
)
def test_property_12_successful_tool_result_is_well_formed(
    now_ms, future_days, holding_horizon
):
    """Feature: earnings-event-risk-gate, Property 12: A successful tool result
    is well-formed — with the source and clock MOCKED and the gate enabled, a
    non-unavailable, non-error ``get_event_risk`` result carries a finite
    non-negative-or-null ``days_until_event``, an ``event_risk`` in
    EVENT_RISK_STATES, an ``event_recommendation`` in EVENT_RECOMMENDATIONS, and
    a string ``event_date``.

    Validates: Requirements 4.5
    """
    # A strictly-future candidate: adding a positive whole-day offset PLUS one
    # hour guarantees the candidate is strictly after the reference "now", so
    # select_next_event always yields it (never a past / at-reference event).
    candidate_ms = now_ms + future_days * _DAY_MS + _HOUR_MS

    def _fake_load(symbol, config):
        # A configured source that read cleanly and yielded one upcoming event.
        return {
            "candidates": [candidate_ms],
            "source_configured": True,
            "retrieval_failed": False,
            "failure_reason": None,
        }

    # Mock BOTH the source reader and the process clock so the tool runs
    # deterministically in-memory with no network / filesystem access.
    with mock.patch.object(tools, "_load_event_candidates", side_effect=_fake_load), \
            mock.patch.object(tools.time, "time", return_value=now_ms / 1000.0):
        result = _raw(get_event_risk)(symbol=_SYMBOL, holding_horizon=holding_horizon)

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # This property asserts only over SUCCESSFUL assessments; the gate being
    # disabled by the ambient environment (or any degradation) yields an
    # Unavailable_Marker / error which is out of scope here.
    assume("unavailable" not in result)
    assume("error" not in result)

    # days_until_event is a finite non-negative number or null.
    assert "days_until_event" in result, "days_until_event missing"
    assert _is_finite_nonneg_or_null(result["days_until_event"]), (
        f"days_until_event is neither a finite non-negative number nor null: "
        f"{result['days_until_event']!r}"
    )

    # event_risk ∈ EVENT_RISK_STATES (the three-value enum).
    assert result.get("event_risk") in EVENT_RISK_STATES, (
        f"event_risk {result.get('event_risk')!r} not in {EVENT_RISK_STATES}"
    )

    # event_recommendation ∈ EVENT_RECOMMENDATIONS (the four-value enum).
    assert result.get("event_recommendation") in EVENT_RECOMMENDATIONS, (
        f"event_recommendation {result.get('event_recommendation')!r} "
        f"not in {EVENT_RECOMMENDATIONS}"
    )

    # event_date is a string identifying the reference Scheduled_Event date.
    assert isinstance(result.get("event_date"), str), (
        f"event_date is not a string: {result.get('event_date')!r}"
    )
