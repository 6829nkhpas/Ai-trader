# Feature: earnings-event-risk-gate, Property 1: Classification is deterministic
"""Property-based test for deterministic event classification (events.py, task 2.3).

Feature: earnings-event-risk-gate

This module implements design **Property 1: Classification is deterministic**:

    For an identical (reference_ms, event_ms, holding_horizon, config) tuple,
    repeated invocations of ``assess_event_risk`` return an identical result —
    an identical Event_Assessment when the input is valid, or an identical
    Unavailable_Marker when the input is invalid. The classifier is pure: it
    derives its result solely from its arguments and the resolved config, never
    reads the host clock, and never mutates its inputs, so no call order or
    number of prior calls can change the answer.

Validates: Requirements 2.8, 3.4.

The strategies generate a deliberately broad mix of inputs so BOTH branches are
exercised many times:

  * ``reference_ms`` / ``event_ms``: finite epoch-millisecond timestamps (valid),
    plus the invalid values the classifier must reject deterministically —
    ``None``, ``NaN``, ``+inf`` / ``-inf``, booleans, and non-numeric strings.
  * ``holding_horizon``: the recognized ``intraday`` / ``multi_session`` values,
    unrecognized text, ``None``, and non-string values (all normalize).
  * ``config``: arbitrary ``EventConfig`` values spanning loadable and
    unloadable timezones, varying (ordered) window widths, and both source
    fields set / unset.

Determinism must hold no matter which category each value falls into, so every
path (valid assessment, invalid -> marker, timezone/date fallbacks) is covered.

The sys.path / import bootstrap mirrors
``tests/test_event_config_deterministic_properties.py``.
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


# ── Input strategies ──────────────────────────────────────────────────────────

# A representative span of finite epoch-millisecond timestamps: the epoch, a
# recent-ish range around 2020-2025, plus small/large magnitudes and fractional
# millis. ``datetime.fromtimestamp`` handles these across the tested timezones.
_finite_ms = st.one_of(
    st.integers(min_value=0, max_value=4_102_444_800_000),          # 1970 .. ~2100
    st.integers(min_value=1_500_000_000_000, max_value=1_800_000_000_000),  # ~2017..2027
    st.floats(
        min_value=0.0,
        max_value=4_102_444_800_000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)

# The invalid timestamp values the classifier must reject deterministically.
_invalid_ms = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.booleans(),                                    # bool is excluded by _is_finite_number
    st.text(alphabet="abc0123:-/", min_size=0, max_size=6),  # non-numeric
    st.just(10 ** 30),                                # far out of datetime range
    st.just(-(10 ** 18)),                             # deeply negative
)

# Timestamp arg: mostly finite (valid path), sometimes invalid (marker path).
_ts_arg = st.one_of(_finite_ms, _finite_ms, _invalid_ms)

# Holding_Horizon: recognized values, unrecognized text, None, non-string.
_horizon_arg = st.one_of(
    st.sampled_from(sorted(HOLDING_HORIZONS)),                 # valid
    st.sampled_from(["swing", "positional", "scalp", "MULTI_SESSION", ""]),  # unrecognized
    st.none(),                                                 # absent
    st.integers(),                                             # non-string
    st.just(True),                                             # non-string bool
)

# Optional context passed through onto the assessment / marker verbatim.
_optional_str_arg = st.one_of(
    st.none(),
    st.sampled_from(["RELIANCE", "TCS", "INFY", "2025-01-15", ""]),
)

# EventConfig: arbitrary but structurally-valid resolved configs. Timezones span
# loadable IANA zones and unloadable names (the classifier degrades to a marker
# on an unloadable tz — still deterministic). Windows are ordered
# (through_event <= imminent) to mirror the resolver invariant, but determinism
# does not depend on that.
_tz_value = st.sampled_from(
    ["Asia/Kolkata", "UTC", "America/New_York", "Europe/London", "Not/AZone", "garbage"]
)


@st.composite
def _event_configs(draw):
    imminent = draw(st.integers(min_value=0, max_value=30))
    through = draw(st.integers(min_value=0, max_value=imminent))
    return EventConfig(
        enabled=draw(st.booleans()),
        timezone=draw(_tz_value),
        default_holding_horizon=draw(st.sampled_from(sorted(HOLDING_HORIZONS))),
        imminent_window_days=imminent,
        through_event_window_days=through,
        source_timeout_s=draw(
            st.floats(min_value=0.001, max_value=120.0, allow_nan=False, allow_infinity=False)
        ),
        calendar_api_url=draw(st.one_of(st.none(), st.sampled_from(["https://x.test/cal"]))),
        calendar_file_path=draw(st.one_of(st.none(), st.sampled_from(["/tmp/cal.json"]))),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (task 2.3): Classification is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 1: Classification is deterministic
@settings(max_examples=200, deadline=None)
@given(
    reference_ms=_ts_arg,
    event_ms=_ts_arg,
    holding_horizon=_horizon_arg,
    config=_event_configs(),
    symbol=_optional_str_arg,
    event_date=_optional_str_arg,
)
def test_property_1_classification_is_deterministic(
    reference_ms, event_ms, holding_horizon, config, symbol, event_date
):
    """Validates: Requirements 2.8, 3.4

    For an identical (reference, event, horizon, config, context) tuple,
    ``assess_event_risk`` returns identical results across repeated calls —
    identical Event_Assessment for valid input, identical Unavailable_Marker for
    invalid input. Called three times to guard against any dependence on call
    order or the number of prior invocations.
    """
    first = assess_event_risk(
        reference_ms, event_ms, holding_horizon, config, symbol=symbol, event_date=event_date
    )
    second = assess_event_risk(
        reference_ms, event_ms, holding_horizon, config, symbol=symbol, event_date=event_date
    )
    third = assess_event_risk(
        reference_ms, event_ms, holding_horizon, config, symbol=symbol, event_date=event_date
    )

    # Always returns a dict (assessment or marker) — never raises (R3.1).
    assert isinstance(first, dict)

    # Determinism: repeated calls yield byte-for-byte identical results whether
    # the tuple produced a valid assessment or an honest Unavailable_Marker.
    assert first == second
    assert first == third

    # The two branches are mutually exclusive and both deterministic in shape:
    # a marker omits event_risk/event_recommendation (R5.1, R12.1); an
    # assessment carries all six fields (R2.8).
    if first.get("unavailable"):
        assert "event_risk" not in first
        assert "event_recommendation" not in first
    else:
        assert set(first) == {
            "days_until_event",
            "event_risk",
            "event_recommendation",
            "holding_horizon",
            "event_date",
            "symbol",
        }


# Feature: earnings-event-risk-gate, Property 1: Classification is deterministic
@settings(max_examples=200, deadline=None)
@given(
    reference_ms=_ts_arg,
    event_ms=_ts_arg,
    holding_horizon=_horizon_arg,
    config=_event_configs(),
)
def test_property_1_inputs_are_never_mutated(reference_ms, event_ms, holding_horizon, config):
    """Validates: Requirements 2.8, 3.4

    Determinism relies on the classifier being pure — it must never mutate its
    inputs (R2.9), otherwise a later call could observe a changed argument. Snap
    a deep copy of every argument, invoke, and assert the arguments are
    unchanged (NaN is compared reflexively since ``NaN != NaN``).
    """
    ref_before = copy.deepcopy(reference_ms)
    event_before = copy.deepcopy(event_ms)
    horizon_before = copy.deepcopy(holding_horizon)
    config_before = copy.deepcopy(config)

    assess_event_risk(reference_ms, event_ms, holding_horizon, config)

    def _same(after, before):
        if isinstance(after, float) and isinstance(before, float) \
                and math.isnan(after) and math.isnan(before):
            return True
        return after == before and type(after) is type(before)

    assert _same(reference_ms, ref_before)
    assert _same(event_ms, event_before)
    assert _same(holding_horizon, horizon_before)
    # EventConfig is frozen; equality confirms no field was rebound.
    assert config == config_before
