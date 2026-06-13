"""Property-based test for the forecaster scope boundary (graph.py, task 9.5).

Feature: volatility-aware-forecaster

This module implements design **Property 39: The forecast never modifies or
blocks a committed decision**:

    For any committed Deep_Quant_Agent decision (arbitrary BUY/SELL/HOLD action
    and arbitrary execution levels — entry / stop_loss / take_profit) and ANY
    ``get_forecast`` result present in message history — a conforming
    Forecast_Label of any alignment (aligned / misaligned / neutral) OR an
    Unavailable_Marker — assembling the defensibility record via
    ``build_defensibility_record`` leaves the committed decision's action and
    execution levels byte-for-byte UNCHANGED, and the record reports the
    committed action verbatim (the forecast never flips or blocks it). Even when
    the forecast is ``misaligned``, the trade is NOT blocked: the record is still
    produced and the committed action stands. The forecast's effect is limited
    to prompt guidance and defensibility surfacing (R15.4, R15.5).

Validates: Requirements 15.4, 15.5.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` reads
    the most-recent ``get_forecast`` result and attaches a forecast entry (with
    a ``trade_opposes_forecast`` opposition statement when ``misaligned`` + a
    directional action) WITHOUT touching the decision's action or levels and
    WITHOUT blocking the trade.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Tool results are
serialized both as JSON and as Python dict-repr strings, since both quoting
styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_forecast_defensibility_opposition_properties.py``: the service
directory (one level up) is prepended to ``sys.path`` so ``graph`` is importable
when pytest is run from anywhere.
"""

import copy
import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import build_defensibility_record  # noqa: E402

FORECAST_TOOL = "get_forecast"

# The committed-decision fields the forecast must never touch.
_EXECUTION_LEVEL_FIELDS = ("entry", "stop_loss", "take_profit")


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol/timeframe restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so a label is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_projected_direction = st.sampled_from(["up", "down", "flat"])
# All three alignments PLUS the unavailable case are exercised below.
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# Probabilities / confidences are finite numbers in [0.0, 1.0].
_unit_value = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
# expected_move_atr is a finite number or null (None), per the forecast contract.
_expected_move_atr = st.one_of(
    st.none(),
    st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)
# A measure value is a finite number or null (None).
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
_nonneg_measure = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
)
# Finite execution levels (a committed plan carries real numbers).
_level = st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)


@st.composite
def _forecast_label(draw):
    """Draw a conforming Forecast_Label of any alignment."""
    return {
        "projected_direction": draw(_projected_direction),
        "up_probability": draw(_unit_value),
        "expected_move_atr": draw(_expected_move_atr),
        "forecast_confidence": draw(_unit_value),
        "forecast_alignment": draw(_alignment),
        "measures": {
            "drift": draw(_measure_value),
            "volatility": draw(_nonneg_measure),
            "standardized_drift": draw(_measure_value),
            "atr": draw(_nonneg_measure),
        },
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }


@st.composite
def _forecast_unavailable(draw):
    """Draw an Unavailable_Marker (no fabricated forecast fields)."""
    return {
        "unavailable": True,
        "reason": draw(st.sampled_from([
            "insufficient candles: received 12, required 30",
            "candle retrieval failed",
            "regime classifier unavailable",
        ])),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }


# A get_forecast result is EITHER a usable label OR an unavailable marker.
_forecast_result = st.one_of(_forecast_label(), _forecast_unavailable())


@st.composite
def _committed_decision(draw):
    """Draw a committed decision with an arbitrary action and execution levels."""
    decision = {
        "action": draw(_action),
        "source": "declare_trade",
    }
    for field in _EXECUTION_LEVEL_FIELDS:
        decision[field] = draw(_level)
    return decision


# ─────────────────────────────────────────────────────────────────────────────
# Property 39: the forecast never modifies or blocks a committed decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 39: The forecast never modifies or blocks a committed decision
@settings(max_examples=200, deadline=None)
@given(
    decision=_committed_decision(),
    result=_forecast_result,
    style=_serialization_style,
)
def test_property_39_forecast_never_modifies_or_blocks_decision(decision, result, style):
    """Validates: Requirements 15.4, 15.5

    For ANY committed decision (BUY/SELL/HOLD + execution levels) and ANY
    ``get_forecast`` result (a Forecast_Label of any alignment — aligned /
    misaligned / neutral — or an Unavailable_Marker):

      * ``build_defensibility_record`` NEVER raises;
      * the committed decision's action and execution levels
        (entry / stop_loss / take_profit) are byte-for-byte unchanged
        (``== `` the deep-copied snapshot taken before the build) — R15.4;
      * the record's resolved/committed action equals the input action (the
        forecast did not flip or block it) — R15.4;
      * even when the forecast is ``misaligned``, the trade is NOT blocked: the
        record is still produced and the committed action stands — R15.5.
    """
    messages = [StubToolMessage(content=_serialize(result, style), name=FORECAST_TOOL)]

    # Deep-copy the committed decision BEFORE building the record.
    action_before = decision["action"]
    decision_before = copy.deepcopy(decision)

    # Build never raises, for any forecast result / committed action combo.
    record = build_defensibility_record(messages, decision, mode="FIND")

    # ── The committed decision is byte-for-byte unchanged (R15.4) ────────────
    # Action unchanged.
    assert decision["action"] == action_before
    # Every execution level unchanged (byte-for-byte == the deepcopy).
    for field in _EXECUTION_LEVEL_FIELDS:
        assert decision[field] == decision_before[field]
    # The whole committed decision object is untouched (no field added/removed).
    assert decision == decision_before, (
        "record-building must not modify the committed decision; "
        f"before={decision_before!r} after={decision!r}"
    )

    # ── The record surfaces, never overrides or blocks, the decision ─────────
    # The record reports the committed action verbatim — the forecast did not
    # flip it to a different action and did not block it (R15.4).
    assert record["action"] == action_before

    # A forecast entry is present as defensibility surfacing only. Even when the
    # forecast is misaligned, the trade is NOT blocked: the record is produced
    # and the committed action stands (R15.5).
    assert "forecast" in record
    forecast = record["forecast"]

    is_label = result.get("unavailable") is not True
    if is_label and result["forecast_alignment"] == "misaligned":
        # A misaligned forecast against a directional trade adds an opposition
        # STATEMENT, but that statement is surfacing only — it never blocks the
        # trade nor alters the committed action/levels.
        if action_before in ("BUY", "SELL"):
            assert isinstance(forecast.get("trade_opposes_forecast"), str)
        # The committed action still stands despite the misalignment (R15.5).
        assert record["action"] == action_before
