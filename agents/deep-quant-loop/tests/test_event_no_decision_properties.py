# Feature: earnings-event-risk-gate, Property 10: The classifier emits only an assessment or a marker — never a trade decision
"""Property-based test that the Event_Classifier never emits a trade decision (events.py, task 2.12).

Feature: earnings-event-risk-gate

This module implements design **Property 10: The classifier emits only an
assessment or a marker — never a trade decision**:

    ``events.assess_event_risk(...)`` produces ONLY an Event_Assessment or an
    Unavailable_Marker. Its result NEVER carries a trade-decision field — no
    ``action`` (BUY/SELL/HOLD), no ``decision`` / ``conviction`` /
    ``conviction_score``, no ``side`` / ``order`` / ``trade``, no ``entry`` /
    ``stop_loss`` / ``take_profit`` — and no string value anywhere within the
    result equals a BUY / SELL / HOLD action. The Event_Classifier is a risk
    filter / context aid, never a trade generator (Requirement 12).

Validates: Requirements 12.1.

Reference / event timestamps and Holding_Horizons are generated so that BOTH the
Event_Assessment path (valid, finite, non-past timestamps) and the
Unavailable_Marker path (missing / non-numeric / non-finite / out-of-range /
past-dated timestamps) are exercised. Horizons include the two recognized values
and a variety of unrecognized junk (which normalizes to the configured default).
The resolved configuration is drawn arbitrarily (windows kept ordering-consistent
as ``resolve_event_config`` guarantees). The sys.path / import pattern mirrors the
sibling ``test_event_*_properties.py`` modules.

Note: ``symbol`` / ``event_date`` are caller-echoed context. They are constrained
here to non-action-word values, because the property concerns the values the
classifier *produces*, not arbitrary caller-supplied action strings echoed back.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from events import (  # noqa: E402
    EventConfig,
    assess_event_risk,
)

# The complete set of keys either result shape is permitted to carry. An
# Event_Assessment carries the six-field superset; an Unavailable_Marker carries
# ``unavailable`` / ``reason`` plus whatever context (symbol / holding_horizon /
# event_date) the caller supplied. Their union is the allow-list; anything else
# is an unexpected (potentially decision-like) key.
_ALLOWED_KEYS = frozenset(
    {
        "days_until_event",
        "event_risk",
        "event_recommendation",
        "holding_horizon",
        "event_date",
        "symbol",
        "unavailable",
        "reason",
    }
)

# Trade-decision fields the classifier must NEVER carry (Requirement 12.1). It
# emits an assessment or a marker only — never a decision.
_FORBIDDEN_KEYS = frozenset(
    {
        "action",
        "decision",
        "conviction",
        "conviction_score",
        "side",
        "order",
        "trade",
        "entry",
        "stop_loss",
        "take_profit",
        "signal",
        "buy",
        "sell",
        "hold",
    }
)

# BUY / SELL / HOLD action words that must not appear as a value anywhere in the
# result (compared case-insensitively after stripping).
_ACTION_WORDS = frozenset({"BUY", "SELL", "HOLD"})

_HORIZONS = ["intraday", "multi_session"]

# A reference epoch-ms roughly spanning 1970..~2065 keeps generated valid
# timestamps inside the representable datetime range.
_VALID_MS = st.floats(
    min_value=0.0, max_value=3.0e12, allow_nan=False, allow_infinity=False
)

# Invalid timestamps drive the Unavailable_Marker path: None, non-finite,
# non-numeric, and extreme out-of-range magnitudes.
_INVALID_MS = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.text(max_size=6),
    st.booleans(),
    st.floats(min_value=1.0e19, max_value=1.0e30, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-1.0e30, max_value=-1.0e19, allow_nan=False, allow_infinity=False),
)

_ANY_MS = st.one_of(_VALID_MS, _INVALID_MS)

# Holding_Horizon: recognized values plus unrecognized junk (which normalizes to
# the configured default). None / blanks / mixed case / non-strings all covered.
_HORIZON_INPUT = st.one_of(
    st.none(),
    st.sampled_from(_HORIZONS),
    st.sampled_from(["Intraday", "MULTI_SESSION", "", "   ", "swing", "position"]),
    st.integers(),
    st.text(max_size=8),
)

# Caller-echoed context, constrained to non-action-word values (see module docstring).
_SYMBOL = st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS", "INFY", "NIFTY", ""]))
_EVENT_DATE = st.one_of(
    st.none(), st.sampled_from(["2025-01-15", "2025-06-30", "2024-12-01", ""])
)


@st.composite
def _config(draw):
    """An arbitrary ``EventConfig`` whose windows satisfy the ordering invariant
    ``through_event_window_days <= imminent_window_days`` that
    ``resolve_event_config`` guarantees. The timezone is a real loadable zone so
    the assessment path is reachable."""
    through = draw(st.integers(min_value=0, max_value=30))
    imminent = draw(st.integers(min_value=through, max_value=60))
    return EventConfig(
        enabled=draw(st.booleans()),
        timezone=draw(st.sampled_from(["Asia/Kolkata", "UTC", "America/New_York"])),
        default_holding_horizon=draw(st.sampled_from(_HORIZONS)),
        imminent_window_days=imminent,
        through_event_window_days=through,
        source_timeout_s=10.0,
        calendar_api_url=None,
        calendar_file_path=None,
    )


def _walk_keys_and_values(obj):
    """Yield ``("key", k)`` for every mapping key and ``("value", v)`` for every
    leaf value reached by recursively walking dicts / lists / tuples in ``obj``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("key", k)
            yield from _walk_keys_and_values(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys_and_values(item)
    else:
        yield ("value", obj)


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (task 2.12): the classifier emits only an assessment or a marker
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 10: The classifier emits only an assessment or a marker — never a trade decision
@settings(max_examples=300, deadline=None)
@given(
    reference_ms=_ANY_MS,
    event_ms=_ANY_MS,
    holding_horizon=_HORIZON_INPUT,
    config=_config(),
    symbol=_SYMBOL,
    event_date=_EVENT_DATE,
)
def test_property_10_classifier_never_emits_a_trade_decision(
    reference_ms, event_ms, holding_horizon, config, symbol, event_date
):
    """Feature: earnings-event-risk-gate, Property 10: The classifier emits only
    an assessment or a marker — never a trade decision.

    For any (reference, event, horizon, config) tuple — driving both the
    Event_Assessment path and the Unavailable_Marker path — ``assess_event_risk``
    returns a dict that is exactly one of the two allowed shapes, whose keys are a
    subset of the assessment/marker allow-list, that carries no trade-decision
    key at any nesting level, and that contains no BUY/SELL/HOLD action value
    anywhere within it.

    Validates: Requirements 12.1
    """
    result = assess_event_risk(
        reference_ms,
        event_ms,
        holding_horizon,
        config,
        symbol=symbol,
        event_date=event_date,
    )

    # The classifier only ever emits a dict (an assessment or an Unavailable_Marker).
    assert isinstance(result, dict), f"result is not a dict: {result!r}"

    # It is exactly one of the two allowed shapes: an Unavailable_Marker (carries
    # ``unavailable``) OR an Event_Assessment (carries ``event_risk``).
    is_marker = result.get("unavailable") is True
    is_assessment = "event_risk" in result
    assert is_marker ^ is_assessment, (
        f"result is neither a clean marker nor a clean assessment: {result!r}"
    )

    # Every top-level key is on the assessment/marker allow-list — no stray
    # (potentially decision-like) key sneaks in (Requirement 12.1).
    stray = set(result.keys()) - _ALLOWED_KEYS
    assert not stray, f"unexpected non-allow-list keys {stray!r} in result: {result!r}"

    # No trade-decision field appears at any nesting level (Requirement 12.1).
    for kind, item in _walk_keys_and_values(result):
        if kind == "key" and isinstance(item, str):
            assert item.lower() not in _FORBIDDEN_KEYS, (
                f"forbidden trade-decision key {item!r} present in result: {result!r}"
            )

    # No string value anywhere within the result equals a BUY/SELL/HOLD action
    # (Requirement 12.1): the classifier never emits a decision value.
    for kind, item in _walk_keys_and_values(result):
        if kind == "value" and isinstance(item, str):
            assert item.strip().upper() not in _ACTION_WORDS, (
                f"BUY/SELL/HOLD action value {item!r} present in result: {result!r}"
            )

    # Shape-specific guarantees.
    if is_marker:
        # A marker asserts an absence of a usable assessment: it never fabricates
        # an event_risk / event_recommendation (which are also never actions).
        assert "event_risk" not in result
        assert "event_recommendation" not in result
        assert isinstance(result.get("reason"), str) and result["reason"]
    else:
        # An assessment carries only the categorical context fields — the
        # event_risk / event_recommendation vocabularies contain no action word.
        assert result["event_risk"] in {"clear", "imminent", "through_event"}
        assert result["event_recommendation"] in {
            "proceed",
            "size_down",
            "shorten_horizon",
            "stand_aside",
        }
        assert result["holding_horizon"] in _HORIZONS
        days = result["days_until_event"]
        assert isinstance(days, int) and not isinstance(days, bool) and days >= 0
        assert math.isfinite(days)
