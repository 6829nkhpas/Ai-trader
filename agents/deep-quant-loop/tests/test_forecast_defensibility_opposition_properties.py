"""Property-based test for the misaligned-directional opposition statement
(graph.py, task 9.4).

Feature: volatility-aware-forecaster

This module implements design **Property 25: A misaligned directional trade
records the opposition statement**:

    When the most-recent ``get_forecast`` Forecast_Alignment is ``misaligned``
    AND the committed decision's action is BUY or SELL, the forecast entry built
    by ``build_defensibility_record`` includes an explicit
    ``trade_opposes_forecast`` statement (a non-empty string) declaring that the
    committed trade opposes the forecast. Conversely, that statement is ABSENT
    for a HOLD action or for any non-misaligned Forecast_Alignment
    (aligned / neutral).

Validates: Requirements 9.4.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"forecast"`` entry gains the
    ``trade_opposes_forecast`` key only when the forecast alignment is
    ``misaligned`` and the action is directional (BUY/SELL). The statement is
    also surfaced in the human-readable ``summary``.

The forecast is a predictive cross-check / defensibility surface only: building
the record NEVER modifies the committed decision's action or execution levels
(entry, stop-loss, take-profit) — R15.4 / R15.5. This test asserts that too.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Tool results are
serialized both as JSON and as Python dict-repr strings, since both quoting
styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_rs_defensibility_opposition_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``graph`` is importable.
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
# "unavailable" substrings, so the result is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_projected_direction = st.sampled_from(["up", "down", "flat"])
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
# Execution levels carried by the committed decision.
_level = st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)


@st.composite
def _forecast_label(draw, alignment=None):
    """Draw a conforming Forecast_Label, optionally pinning forecast_alignment."""
    return {
        "projected_direction": draw(_projected_direction),
        "up_probability": draw(_unit_value),
        "expected_move_atr": draw(_expected_move_atr),
        "forecast_confidence": draw(_unit_value),
        "forecast_alignment": draw(_alignment) if alignment is None else alignment,
        "measures": {
            "drift": draw(_measure_value),
            "volatility": draw(st.one_of(
                st.none(),
                st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))),
            "standardized_drift": draw(_measure_value),
            "atr": draw(st.one_of(
                st.none(),
                st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))),
        },
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 25: a misaligned directional trade records the opposition statement
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 25: A misaligned directional trade records the opposition statement
@settings(max_examples=200, deadline=None)
@given(
    label=_forecast_label(),
    style=_serialization_style,
    action=_action,
    entry=_level,
    stop_loss=_level,
    take_profit=_level,
)
def test_property_25_misaligned_directional_records_opposition_statement(
    label, style, action, entry, stop_loss, take_profit
):
    """Validates: Requirements 9.4

    The ``trade_opposes_forecast`` opposition statement is present in the
    forecast entry exactly when the most-recent Forecast_Alignment is
    ``misaligned`` AND the committed action is directional (BUY or SELL); it is
    absent for a HOLD action or any non-misaligned alignment (aligned/neutral).
    Building the record never raises, a present statement is surfaced in the
    record summary, and the committed decision's action and execution levels
    (entry/stop_loss/take_profit) are never modified by record-building
    (R15.4 / R15.5 spirit).
    """
    messages = [StubToolMessage(content=_serialize(label, style), name=FORECAST_TOOL)]
    decision = {
        "action": action,
        "source": "declare_trade",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }
    decision_before = copy.deepcopy(decision)

    record = build_defensibility_record(messages, decision, mode="FIND")
    forecast = record["forecast"]

    # The forecast label is usable, so the entry mirrors it (available is True).
    assert forecast.get("available") is True

    should_oppose = label["forecast_alignment"] == "misaligned" and action in ("BUY", "SELL")

    if should_oppose:
        # Statement present and a non-empty string (R9.4).
        statement = forecast.get("trade_opposes_forecast")
        assert isinstance(statement, str) and statement.strip(), (
            f"expected a non-empty opposition statement for action={action}, "
            f"alignment={label['forecast_alignment']}, got {statement!r}"
        )
        # It explicitly declares the committed trade opposes the forecast (R9.4).
        assert "opposes the forecast" in statement
        # It is surfaced in the human-readable summary too.
        assert statement in record["summary"]
    else:
        # Statement absent for HOLD or for aligned/neutral alignment (R9.4).
        assert "trade_opposes_forecast" not in forecast, (
            f"opposition statement must be absent for action={action}, "
            f"alignment={label['forecast_alignment']}, got "
            f"{forecast.get('trade_opposes_forecast')!r}"
        )

    # The forecast NEVER modifies the committed decision's action or execution
    # levels (R15.4 / R15.5): the decision dict is untouched by record-building.
    assert decision == decision_before, (
        "record-building must not modify the committed decision; "
        f"before={decision_before!r} after={decision!r}"
    )
