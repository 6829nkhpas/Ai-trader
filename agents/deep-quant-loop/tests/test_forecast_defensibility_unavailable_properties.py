"""Property-based test for the absent-forecast defensibility entry.

Feature: volatility-aware-forecaster (graph.py, task 9.3)

This module implements design **Property 24: Absent forecast is recorded as
unavailable**:

    When no usable ``get_forecast`` result is present in message history — none
    present at all, only error results ``{"error": ...}``, only Unavailable_
    Markers ``{"unavailable": true, "reason": ...}``, a non-dict result, or a
    label-shaped result missing/with-invalid fields (out-of-enum
    projected_direction / forecast_alignment, or non-finite up_probability /
    forecast_confidence) — the defensibility forecast entry is recorded as
    ``{"available": False, ...}`` with NO fabricated projected_direction,
    up_probability, expected_move_atr, forecast_confidence, forecast_alignment,
    or measures, and the record build never raises.

Validates: Requirements 9.3.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` — the
    top-level record builder, whose ``record["forecast"]`` entry is asserted.
  - ``_forecast_entry(results)`` / ``_latest_tool_results(messages)`` — the
    underlying helpers, asserted directly as a second, lower-level check.

``_latest_tool_results`` SKIPS error results (those carrying an ``error``
marker), so an error-only history yields no ``get_forecast`` entry at all; an
Unavailable_Marker is an honest non-fatal result that passes through and is
recognised by ``_forecast_entry`` via its ``unavailable: true`` flag; a non-dict
result and a label missing/with-invalid fields are both treated as "no usable
label". In every one of these cases the forecast entry must be
``{"available": False, "reason": ...}`` with the projection fields and measures
ABSENT.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Results are
serialized both as JSON (``{"...": ...}``) and as Python dict/list-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors the sibling
``tests/test_rs_defensibility_absent_properties.py``: the service directory (one
level up) is prepended to ``sys.path`` so ``graph`` is importable when pytest is
run from anywhere.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    build_defensibility_record,
    _latest_tool_results,
    _forecast_entry,
)

FORECAST_TOOL = "get_forecast"

# Projection fields / measures that MUST be absent from an unavailable forecast
# entry (no fabrication — R9.3, R6.3).
_FORBIDDEN_KEYS = (
    "projected_direction",
    "up_probability",
    "expected_move_atr",
    "forecast_confidence",
    "forecast_alignment",
    "measures",
    "trade_opposes_forecast",
)


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a LangChain ToolMessage. ``_is_tool_message`` matches type 'tool'."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _serialize(payload, style):
    """Serialize a result object as a JSON string or a Python repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python repr: single quotes, True/None tokens


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol restricted to tokens that can never contain the "error" / "unavailable"
# substrings, so the classification of each forecast result is decided purely by
# its structure, not incidental free text.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# Reason strings deliberately free of the substring "error" so an Unavailable_
# Marker is never misclassified as an error result.
_unavailable_reason = st.sampled_from([
    "insufficient valid candles: 12 received, 30 required",
    "insufficient valid candles: 0 received, 30 required",
    "failed to retrieve candles: timeout",
    "no usable returns could be computed",
    "candle source returned no data",
])
_error_text = st.sampled_from([
    "Failed to retrieve candles from Rust server: timeout",
    "connection refused",
    "contract_violation",
    "no data",
])

# Invalid categorical values — never the legitimate enum members, and never the
# substrings "error"/"unavailable" — so the result is a parsed dict that fails
# the usable-label check in _forecast_entry.
_bad_direction = st.sampled_from(["rising", "sideways", "UP", "", "bull", "long"])
_bad_alignment = st.sampled_from(["with", "against", "ALIGNED", "", "fighting"])
# Non-finite / non-numeric probability and confidence values: each must make the
# label fail the _is_finite_num check.
_non_finite_num = st.sampled_from([float("nan"), float("inf"), float("-inf"), None, "0.6", True])


@st.composite
def _error_forecast_msg(draw):
    """A get_forecast error result message (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "error": draw(_error_text),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), FORECAST_TOOL)


@st.composite
def _unavailable_forecast_msg(draw):
    """A get_forecast Unavailable_Marker result message (R6.3 shape: omits
    projected_direction/up_probability/expected_move_atr/forecast_confidence/
    forecast_alignment)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "unavailable": True,
        "reason": draw(_unavailable_reason),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), FORECAST_TOOL)


@st.composite
def _nondict_forecast_msg(draw):
    """A get_forecast result that parses to a NON-dict object (list/scalar).

    ``_forecast_entry`` treats any non-dict result as "no usable label"."""
    payload = draw(st.sampled_from([
        [1, 2, 3],
        [],
        ["up", "0.6", "aligned"],
        42,
    ]))
    return StubToolMessage(_serialize(payload, draw(_style)), FORECAST_TOOL)


@st.composite
def _invalid_label_forecast_msg(draw):
    """A label-shaped get_forecast result with a missing/invalid field
    (out-of-enum projected_direction/forecast_alignment, or non-finite
    up_probability/forecast_confidence). None of these is a usable
    Forecast_Label, so the entry must be recorded as unavailable WITHOUT
    fabricating the projection fields."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "projected_direction": draw(_bad_direction),
        "up_probability": draw(_non_finite_num),
        "expected_move_atr": draw(st.sampled_from([0.41, None, -0.2])),
        "forecast_confidence": draw(_non_finite_num),
        "forecast_alignment": draw(_bad_alignment),
        "measures": {
            "drift": 0.0012,
            "volatility": 0.0089,
            "standardized_drift": 0.135,
            "atr": 18.4,
        },
    }
    # Randomly drop one of the required keys entirely to exercise the
    # "missing field" branch in addition to the "invalid value" branch.
    drop = draw(st.sampled_from([
        None,
        "projected_direction",
        "up_probability",
        "forecast_confidence",
        "forecast_alignment",
    ]))
    if drop is not None:
        payload.pop(drop, None)
    return StubToolMessage(_serialize(payload, draw(_style)), FORECAST_TOOL)


@st.composite
def _noise_msg(draw):
    """A non-forecast tool result message (never a get_forecast label)."""
    name = draw(st.sampled_from(["get_multi_tf_trend", "get_consensus_report", "get_support_resistance"]))
    if name == "get_multi_tf_trend":
        payload = {"symbol": draw(_symbol), "trend_1h": "Bullish", "trend_4h": "Bullish", "trend_1d": "Neutral"}
    elif name == "get_consensus_report":
        payload = {"symbol": draw(_symbol), "current_price": 2450.5, "rsi_14": 38.2, "atr_14": 18.0}
    else:
        payload = {"pivot": 2445.0, "s1": 2440.0, "r1": 2470.0}
    return StubToolMessage(_serialize(payload, draw(_style)), name)


def _assert_unavailable(entry):
    """The forecast entry must be unavailable with NO fabricated fields."""
    assert isinstance(entry, dict)
    # Recorded as unavailable (available is exactly False, not truthy/missing).
    assert entry.get("available") is False
    # An honest reason is carried.
    assert isinstance(entry.get("reason"), str) and entry["reason"]
    # NONE of the projection fields or measures may be fabricated (R9.3 / R6.3).
    for key in _FORBIDDEN_KEYS:
        assert key not in entry, f"unavailable forecast entry must not contain {key!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 24: Absent forecast is recorded as unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 24: Absent forecast is recorded as unavailable
@settings(max_examples=100, deadline=None)
@given(
    # 0+ forecast results, each NON-usable: an error, an Unavailable_Marker, a
    # non-dict result, or a label missing/with-invalid fields. An empty list
    # models the "no get_forecast result at all" case.
    fc_msgs=st.lists(
        st.one_of(
            _error_forecast_msg(),
            _unavailable_forecast_msg(),
            _nondict_forecast_msg(),
            _invalid_label_forecast_msg(),
        ),
        min_size=0,
        max_size=4,
    ),
    noise=st.lists(_noise_msg(), min_size=0, max_size=3),
    noise_first=st.booleans(),
    action=_action,
)
def test_property_24_absent_forecast_recorded_unavailable(fc_msgs, noise, noise_first, action):
    """Validates: Requirements 9.3

    For any message history containing NO usable get_forecast label (none
    present, or only error / Unavailable_Marker / non-dict / invalid-label
    results), the defensibility forecast entry is recorded as unavailable with no
    fabricated projected_direction/up_probability/expected_move_atr/
    forecast_confidence/forecast_alignment/measures, and the build never raises.
    """
    messages = (noise + fc_msgs) if noise_first else (fc_msgs + noise)

    decision = {
        "action": action,
        "conviction_score": 60,
        "setup_validation": "Setup reviewed.",
        "execution_plan": f"{action} at market",
    }

    # ── Top-level record: never raises, forecast entry recorded unavailable ───
    record = build_defensibility_record(messages, decision, mode="FIND")
    assert "forecast" in record
    _assert_unavailable(record["forecast"])

    # The summary surfaces the forecast as unavailable (no fabricated alignment).
    assert "Forecast: unavailable" in record["summary"]

    # ── Lower-level helpers: same outcome via the documented call path ────────
    results = _latest_tool_results(messages)
    # Error results are skipped entirely; only a non-error result (Unavailable_
    # Marker, non-dict, or invalid label) may surface here — never a usable label.
    fc_result = results.get(FORECAST_TOOL)
    if isinstance(fc_result, dict):
        usable = (
            fc_result.get("unavailable") is not True
            and fc_result.get("projected_direction") in graph.FORECAST_DIRECTIONS
            and fc_result.get("forecast_alignment") in graph.ALIGNMENT_VALUES
            and graph._is_finite_num(fc_result.get("up_probability"))
            and graph._is_finite_num(fc_result.get("forecast_confidence"))
        )
        assert not usable, "no usable Forecast_Label should surface from a non-usable history"

    entry = _forecast_entry(results)
    _assert_unavailable(entry)
