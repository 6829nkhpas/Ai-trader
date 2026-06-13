"""Property-based test for the absent-relative-strength defensibility entry.

Feature: relative-strength-context (graph.py, task 8.3)

This module implements design **Property 21: Absent relative strength is
recorded as unavailable**:

    When no usable ``get_relative_strength`` result is present in message history
    — none present at all, only error results ``{"error": ...}``, only
    Unavailable_Markers ``{"unavailable": true}``, a non-dict result, or a
    label-shaped result missing/with-invalid categorical enum fields — the
    defensibility relative-strength entry is recorded as unavailable with NO
    fabricated index_direction, relative_strength_state, alignment, measures, or
    benchmark, and the record build never raises.

Validates: Requirements 8.3.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` — the
    top-level record builder, whose ``record["relative_strength"]`` entry is
    asserted here.
  - ``_relative_strength_entry(results)`` / ``_latest_tool_results(messages)`` —
    the underlying helpers, asserted directly as a second, lower-level check.

``_latest_tool_results`` SKIPS error results (those carrying an ``error``
marker), so an error-only history yields no ``get_relative_strength`` entry at
all; an Unavailable_Marker is an honest non-fatal result that passes through and
is recognised by ``_relative_strength_entry`` via its ``unavailable: true``
flag; a non-dict result and a label missing/with-invalid enum fields are both
treated as "no usable label". In every one of these cases the relative-strength
entry must be ``{"available": False, "reason": ...}`` with the categorical
states, measures, and benchmark ABSENT.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Results are
serialized both as JSON (``{"...": ...}``) and as Python dict/list-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_defensibility_record.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``graph``
is importable when pytest is run from anywhere.
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
    _relative_strength_entry,
)

RS_TOOL = "get_relative_strength"

# Categorical states / measures / benchmark that MUST be absent from an
# unavailable relative-strength entry (no fabrication).
_FORBIDDEN_KEYS = (
    "index_direction",
    "relative_strength_state",
    "alignment",
    "measures",
    "benchmark",
    "trade_opposes_relative_strength",
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
# Symbol/benchmark restricted to tokens that can never contain the "error" /
# "unavailable" substrings, so the classification of each relative-strength
# result is decided purely by its structure, not incidental free text.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_benchmark = st.sampled_from(["NIFTY 50", "BANKNIFTY", "FINNIFTY"])
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# Reason strings deliberately free of the substring "error" so an Unavailable_
# Marker is never misclassified as an error result.
_unavailable_reason = st.sampled_from([
    "insufficient aligned data: 12 aligned candles available, 31 required",
    "missing benchmark candles for BANKNIFTY",
    "retrieval timeout",
    "no measure could be computed",
    "insufficient aligned data: 0 aligned candles available, 31 required",
])
_error_text = st.sampled_from([
    "Failed to retrieve candles from Rust server: timeout",
    "connection refused",
    "contract_violation",
    "no data",
])

# Invalid categorical values — never the legitimate enum members, and never the
# substrings "error"/"unavailable" — so the result is a parsed dict that fails
# the usable-label check in _relative_strength_entry.
_bad_direction = st.sampled_from(["rising", "sideways", "UP", "", "bull"])
_bad_state = st.sampled_from(["strong", "weak", "LEADER", "", "outperformer"])
_bad_alignment = st.sampled_from(["with", "against", "ALIGNED", "", "fighting"])


@st.composite
def _error_rs_msg(draw):
    """A get_relative_strength error result message (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "benchmark": draw(_benchmark),
        "error": draw(_error_text),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), RS_TOOL)


@st.composite
def _unavailable_rs_msg(draw):
    """A get_relative_strength Unavailable_Marker result message (R5.3 shape:
    omits index_direction/relative_strength_state/alignment)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "benchmark": draw(_benchmark),
        "unavailable": True,
        "reason": draw(_unavailable_reason),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), RS_TOOL)


@st.composite
def _nondict_rs_msg(draw):
    """A get_relative_strength result that parses to a NON-dict object (list/scalar).

    ``_relative_strength_entry`` treats any non-dict result as "no usable label".
    """
    payload = draw(st.sampled_from([
        [1, 2, 3],
        [],
        ["leader", "up", "aligned"],
        42,
    ]))
    return StubToolMessage(_serialize(payload, draw(_style)), RS_TOOL)


@st.composite
def _invalid_label_rs_msg(draw):
    """A label-shaped get_relative_strength result with a missing/invalid enum
    field (out-of-enum direction/state/alignment, or a missing/non-string
    benchmark). None of these is a usable Relative_Strength_Label, so the entry
    must be recorded as unavailable WITHOUT fabricating the categorical states."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "index_direction": draw(_bad_direction),
        "relative_strength_state": draw(_bad_state),
        "alignment": draw(_bad_alignment),
        "measures": {
            "rs_ratio": 0.0142,
            "rs_ratio_slope": 0.00031,
            "relative_return": 0.035,
            "correlation": 0.72,
            "beta": 1.18,
        },
        "benchmark": draw(st.sampled_from([draw(_benchmark), None, 123])),
    }
    # Randomly drop one of the categorical keys entirely to exercise the
    # "missing field" branch in addition to the "invalid value" branch.
    drop = draw(st.sampled_from([None, "index_direction", "relative_strength_state", "alignment", "benchmark"]))
    if drop is not None:
        payload.pop(drop, None)
    return StubToolMessage(_serialize(payload, draw(_style)), RS_TOOL)


@st.composite
def _noise_msg(draw):
    """A non-relative-strength tool result message (never a get_relative_strength label)."""
    name = draw(st.sampled_from(["get_multi_tf_trend", "get_consensus_report", "get_support_resistance"]))
    if name == "get_multi_tf_trend":
        payload = {"symbol": draw(_symbol), "trend_1h": "Bullish", "trend_4h": "Bullish", "trend_1d": "Neutral"}
    elif name == "get_consensus_report":
        payload = {"symbol": draw(_symbol), "current_price": 2450.5, "rsi_14": 38.2, "atr_14": 18.0}
    else:
        payload = {"pivot": 2445.0, "s1": 2440.0, "r1": 2470.0}
    return StubToolMessage(_serialize(payload, draw(_style)), name)


def _assert_unavailable(entry):
    """The relative-strength entry must be unavailable with NO fabricated fields."""
    assert isinstance(entry, dict)
    # Recorded as unavailable (available is exactly False, not truthy/missing).
    assert entry.get("available") is False
    # An honest reason is carried.
    assert isinstance(entry.get("reason"), str) and entry["reason"]
    # NONE of the categorical states, measures, or benchmark may be fabricated.
    for key in _FORBIDDEN_KEYS:
        assert key not in entry, f"unavailable relative-strength entry must not contain {key!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 21: Absent relative strength is recorded as unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 21: Absent relative strength is recorded as unavailable
@settings(max_examples=100, deadline=None)
@given(
    # 0+ relative-strength results, each NON-usable: an error, an
    # Unavailable_Marker, a non-dict result, or a label missing/with-invalid
    # enum fields. An empty list models the "no get_relative_strength result at
    # all" case.
    rs_msgs=st.lists(
        st.one_of(
            _error_rs_msg(),
            _unavailable_rs_msg(),
            _nondict_rs_msg(),
            _invalid_label_rs_msg(),
        ),
        min_size=0,
        max_size=4,
    ),
    noise=st.lists(_noise_msg(), min_size=0, max_size=3),
    noise_first=st.booleans(),
    action=_action,
)
def test_property_21_absent_relative_strength_recorded_unavailable(rs_msgs, noise, noise_first, action):
    """Validates: Requirements 8.3

    For any message history containing NO usable get_relative_strength label
    (none present, or only error / Unavailable_Marker / non-dict / invalid-label
    results), the defensibility relative-strength entry is recorded as
    unavailable with no fabricated index_direction/relative_strength_state/
    alignment/measures/benchmark, and the build never raises.
    """
    messages = (noise + rs_msgs) if noise_first else (rs_msgs + noise)

    decision = {
        "action": action,
        "conviction_score": 60,
        "setup_validation": "Setup reviewed.",
        "execution_plan": f"{action} at market",
    }

    # ── Top-level record: never raises, RS entry recorded as unavailable ──────
    record = build_defensibility_record(messages, decision, mode="FIND")
    assert "relative_strength" in record
    _assert_unavailable(record["relative_strength"])

    # The summary surfaces relative strength as unavailable (no fabricated alignment).
    assert "Relative strength: unavailable" in record["summary"]

    # ── Lower-level helpers: same outcome via the documented call path ────────
    results = _latest_tool_results(messages)
    # Error results are skipped entirely; only a non-error result (Unavailable_
    # Marker, non-dict, or invalid label) may surface here — never a usable label.
    rs_result = results.get(RS_TOOL)
    if isinstance(rs_result, dict):
        usable = (
            rs_result.get("unavailable") is not True
            and rs_result.get("index_direction") in graph.INDEX_DIRECTIONS
            and rs_result.get("relative_strength_state") in graph.RELATIVE_STRENGTH_STATES
            and rs_result.get("alignment") in graph.ALIGNMENT_VALUES
            and isinstance(rs_result.get("benchmark"), str)
        )
        assert not usable, "no usable Relative_Strength_Label should surface from a non-usable history"

    entry = _relative_strength_entry(results)
    _assert_unavailable(entry)
