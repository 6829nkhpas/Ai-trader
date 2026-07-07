# Feature: earnings-event-risk-gate, Property 7: An invalid timestamp yields an Unavailable_Marker, never a fabricated assessment
"""Property-based test for the invalid-timestamp path (events.py, task 2.9).

Feature: earnings-event-risk-gate

This module implements design **Property 7: An invalid timestamp yields an
Unavailable_Marker, never a fabricated assessment**:

    For any missing / non-numeric / non-finite / out-of-range ``reference_ms``
    or ``event_ms`` (on either or both sides), ``assess_event_risk`` returns an
    honest Unavailable_Marker — ``{"unavailable": true, "reason": ...}`` — whose
    reason identifies the invalid-input condition, that OMITS both
    ``event_risk`` and ``event_recommendation`` entirely (no fabricated values,
    AD-5), leaves its inputs unmodified, and never raises.

Validates: Requirements 3.1, 5.1.

The strategies below generate invalid timestamps — ``None``, ``NaN``,
``+-inf``, non-numeric strings (including numeric-looking ones, which are still
strings), ``bool`` (which the module's finite-number check excludes), ``list`` /
``dict`` containers, and out-of-range magnitudes such as ``1e30`` / ``-1e30``
that cannot be represented as a datetime — placed on either or both sides,
together with arbitrary Holding_Horizons and arbitrary, well-formed
``EventConfig`` values. For every such input the result must be an Unavailable_
Marker that never fabricates ``event_risk`` / ``event_recommendation``. A batch
of *valid* (reference, future-event) pairs is mixed in as a control to confirm
they instead yield a full Event_Assessment (so the property is not vacuously
asserting "everything is unavailable").

The sys.path / import bootstrap mirrors ``tests/test_event_days_until_properties.py``
and the invalid-timestamp style mirrors ``tests/test_session_invalid_timestamp_properties.py``.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
from events import EventConfig, HOLDING_HORIZONS, assess_event_risk  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# The two assessment-only fields an invalid input must NEVER fabricate (AD-5,
# R5.1): a marker asserts the *absence* of a usable assessment.
_FABRICATED_KEYS = {"event_risk", "event_recommendation"}

# Decision-shaped keys the classifier must NEVER emit (it is a filter, never a
# trade generator) — asserted defensively so a marker cannot leak one.
_DECISION_KEYS = {"action", "conviction", "decision", "side", "order", "signal"}

_MS_PER_DAY = 86_400_000

# A spread of loadable IANA timezones with materially different UTC offsets.
_TIMEZONES = [
    "Asia/Kolkata",
    "UTC",
    "America/New_York",
    "Europe/London",
    "Asia/Tokyo",
    "Australia/Sydney",
    "America/Los_Angeles",
    "Pacific/Kiritimati",
]

# 2021-01-01 .. ~2031 in epoch milliseconds: comfortably representable as a
# datetime under every configured timezone above.
_VALID_MS = st.integers(min_value=1_609_459_200_000, max_value=1_924_991_999_000)


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Invalid timestamps that must drive the Unavailable_Marker path (R3.1):
#   * None                         -> missing
#   * NaN / +inf / -inf            -> non-finite
#   * non-numeric strings          -> non-numeric (a numeric-looking string is
#                                     still a string, not a number)
#   * bool                         -> excluded from "number" by convention
#   * list / dict                  -> non-numeric containers
#   * 1e30 / -1e30 / 2e30          -> finite but out-of-range for a datetime
_invalid_ms = st.sampled_from(
    [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        "x",
        "",
        "not-a-number",
        "1700000000000",  # numeric-looking, still a string
        True,
        False,
        [],
        [1, 2, 3],
        {},
        {"event_ms": 1700000000000},
        1.0e30,
        -1.0e30,
        2.0e30,
    ]
)

# Arbitrary Holding_Horizon inputs: recognized values, unrecognized strings, and
# non-string values — all must normalize without affecting the marker path.
_any_horizon = st.one_of(
    st.sampled_from(sorted(HOLDING_HORIZONS)),
    st.sampled_from(["", "  ", "swing", "scalp", "MULTI_SESSION", "Intraday"]),
    st.none(),
    st.integers(),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
)

# Optional symbol / event_date context the caller may or may not supply.
_opt_symbol = st.one_of(st.none(), st.sampled_from(["RELIANCE", "INFY", "TCS"]))
_opt_event_date = st.one_of(st.none(), st.sampled_from(["2025-01-15", "2025-06-30"]))


@st.composite
def _config(draw):
    """An arbitrary, well-formed ``EventConfig``.

    Mirrors what ``resolve_event_config`` would produce: a loadable timezone, a
    recognized default Holding_Horizon, non-negative window lengths with the
    ``through_event <= imminent`` ordering, and a strictly-positive source
    timeout. Only shape matters here — the invalid-timestamp path short-circuits
    before any window/timezone math — but a well-formed config keeps the test
    honest.
    """
    imminent = draw(st.integers(min_value=0, max_value=30))
    through = draw(st.integers(min_value=0, max_value=imminent))
    return EventConfig(
        enabled=draw(st.booleans()),
        timezone=draw(st.sampled_from(_TIMEZONES)),
        default_holding_horizon=draw(st.sampled_from(sorted(HOLDING_HORIZONS))),
        imminent_window_days=imminent,
        through_event_window_days=through,
        source_timeout_s=draw(
            st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False)
        ),
        calendar_api_url=draw(st.one_of(st.none(), st.just("https://example.test/cal"))),
        calendar_file_path=draw(st.one_of(st.none(), st.just("/tmp/cal.json"))),
    )


def _assert_no_decision_fields(result):
    """A classifier result must never carry a trade-decision field (R12.1)."""
    assert _DECISION_KEYS.isdisjoint(result.keys()), (
        f"classifier fabricated a decision field: {result!r}"
    )


def _nan_safe_equal(a, b):
    """Equality that treats two NaN floats as equal (for mutation checks)."""
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 7: An invalid timestamp yields an Unavailable_Marker, never a
# fabricated assessment
# ─────────────────────────────────────────────────────────────────────────────


# Feature: earnings-event-risk-gate, Property 7: An invalid timestamp yields an Unavailable_Marker, never a fabricated assessment
@settings(max_examples=25, deadline=None)
@given(
    reference_ms=_VALID_MS,
    event_ms=_VALID_MS,
    bad=_invalid_ms,
    which=st.sampled_from(["reference", "event", "both"]),
    holding_horizon=_any_horizon,
    symbol=_opt_symbol,
    event_date=_opt_event_date,
    config=_config(),
)
def test_property_7_invalid_timestamp_yields_unavailable_marker(
    reference_ms, event_ms, bad, which, holding_horizon, symbol, event_date, config
):
    """Validates: Requirements 3.1, 5.1

    For any missing / non-numeric / non-finite / out-of-range timestamp on
    either (or both) side(s), ``assess_event_risk``:
      * never raises;
      * returns an Unavailable_Marker (``unavailable`` is ``True``) carrying a
        non-empty ``reason`` string;
      * OMITS ``event_risk`` and ``event_recommendation`` entirely (no
        fabricated values, R5.1 / AD-5);
      * carries no trade-decision field (R12.1);
      * leaves its inputs unmodified.
    """
    ref = bad if which in ("reference", "both") else reference_ms
    evt = bad if which in ("event", "both") else event_ms

    ref_snapshot = copy.deepcopy(ref)
    evt_snapshot = copy.deepcopy(evt)
    horizon_snapshot = copy.deepcopy(holding_horizon)
    config_snapshot = copy.deepcopy(config)

    # Never raises (R3.1): the call itself must succeed for any invalid input.
    result = assess_event_risk(
        ref, evt, holding_horizon, config, symbol=symbol, event_date=event_date
    )

    assert isinstance(result, dict), f"expected a dict result, got {result!r}"

    # It is an honest Unavailable_Marker, not an assessment.
    assert result.get("unavailable") is True, (
        f"invalid timestamp ({which}={bad!r}) did not yield an "
        f"Unavailable_Marker: {result!r}"
    )

    # The reason identifies the invalid-input condition (R3.1, R5.1).
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"Unavailable_Marker is missing a reason string: {result!r}"
    )

    # event_risk and event_recommendation are OMITTED entirely (R5.1 / AD-5) —
    # no fabricated assessment values.
    leaked = _FABRICATED_KEYS.intersection(result.keys())
    assert not leaked, (
        f"Unavailable_Marker fabricated assessment field(s) {leaked}: {result!r}"
    )

    # The marker emits no trade decision (R12.1).
    _assert_no_decision_fields(result)

    # Inputs were left unmodified (R2.9 surface: the classifier never mutates).
    assert _nan_safe_equal(ref, ref_snapshot), "assess_event_risk mutated reference_ms"
    assert _nan_safe_equal(evt, evt_snapshot), "assess_event_risk mutated event_ms"
    assert _nan_safe_equal(holding_horizon, horizon_snapshot), (
        "assess_event_risk mutated its holding_horizon input"
    )
    assert config == config_snapshot, "assess_event_risk mutated its config input"

    # The bare (no symbol/event_date) call shape is also an omitting marker.
    bare = assess_event_risk(ref, evt, holding_horizon, config)
    assert bare.get("unavailable") is True
    assert not _FABRICATED_KEYS.intersection(bare.keys())


# Feature: earnings-event-risk-gate, Property 7: An invalid timestamp yields an Unavailable_Marker, never a fabricated assessment
@settings(max_examples=25, deadline=None)
@given(
    reference_ms=_VALID_MS,
    day_offset=st.integers(min_value=0, max_value=400),
    holding_horizon=_any_horizon,
    symbol=_opt_symbol,
    event_date=_opt_event_date,
    config=_config(),
)
def test_property_7_control_valid_timestamps_yield_assessment(
    reference_ms, day_offset, holding_horizon, symbol, event_date, config
):
    """Validates: Requirements 3.1, 5.1 (control)

    Control case: a *valid* (reference, future-event) pair must instead produce
    a full Event_Assessment carrying both ``event_risk`` and
    ``event_recommendation`` (and NOT be an Unavailable_Marker). This guards
    against a vacuous Property 7 in which the classifier marked *everything*
    unavailable.
    """
    event_ms = reference_ms + day_offset * _MS_PER_DAY

    result = assess_event_risk(
        reference_ms, event_ms, holding_horizon, config, symbol=symbol, event_date=event_date
    )

    assert isinstance(result, dict)
    # Not an Unavailable_Marker.
    assert "unavailable" not in result, (
        f"valid (reference, future-event) pair unexpectedly marked unavailable: "
        f"{result!r}"
    )
    # A full assessment carries both of the fields a marker omits.
    assert _FABRICATED_KEYS.issubset(result.keys()), (
        f"valid input produced an incomplete assessment: {result!r}"
    )
    assert result["event_risk"] is not None
    assert result["event_recommendation"] is not None
    # Even a full assessment is never a trade decision (R12.1).
    _assert_no_decision_fields(result)
