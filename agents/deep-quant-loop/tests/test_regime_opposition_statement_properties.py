"""Property-based test for the unfavorable-directional opposition statement
(graph.py, task 8.4).

Feature: regime-detection-gate

This module implements design **Property 20: An unfavorable directional trade
records the opposition statement**:

    When the most-recent ``get_market_regime`` Favorability is ``unfavorable``
    AND the committed decision's action is BUY or SELL, the regime entry built
    by ``build_defensibility_record`` includes an explicit
    ``trade_opposes_regime`` statement (a non-empty string) declaring that the
    trade opposes the regime assessment. Conversely, that statement is ABSENT
    for a HOLD action or for any non-unfavorable Favorability (favorable /
    neutral).

Validates: Requirements 7.4.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"regime"`` entry gains the
    ``trade_opposes_regime`` key only when favorability is ``unfavorable`` and
    the action is directional (BUY/SELL).

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Regime tool results
are serialized both as JSON and as Python dict-repr strings, since both quoting
styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_regime_defensibility_mirror_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``graph`` is importable.
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

from graph import build_defensibility_record  # noqa: E402

REGIME_TOOL = "get_market_regime"


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
_trend_state = st.sampled_from(["trending", "ranging", "transitional"])
_volatility_state = st.sampled_from(["low", "normal", "high"])
_favorability = st.sampled_from(["favorable", "unfavorable", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# A measure value is a finite number or null (None), per the regime contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _regime_label(draw, favorability=None):
    """Draw a conforming Regime_Label, optionally pinning the favorability."""
    return {
        "trend_state": draw(_trend_state),
        "volatility_state": draw(_volatility_state),
        "favorability": draw(_favorability) if favorability is None else favorability,
        "measures": {
            "directional_strength": draw(_measure_value),
            "choppiness": draw(_measure_value),
            "efficiency_ratio": draw(_measure_value),
            "atr_percentile": draw(_measure_value),
            "bb_width": draw(_measure_value),
        },
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "candles_used": draw(st.integers(min_value=1, max_value=500)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 20: an unfavorable directional trade records the opposition statement
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 20
@settings(max_examples=200, deadline=None)
@given(
    label=_regime_label(),
    style=_serialization_style,
    action=_action,
)
def test_property_20_unfavorable_directional_records_opposition_statement(
    label, style, action
):
    """Validates: Requirements 7.4

    The ``trade_opposes_regime`` opposition statement is present in the regime
    entry exactly when the most-recent regime favorability is ``unfavorable``
    AND the committed action is directional (BUY or SELL); it is absent for a
    HOLD action or any non-unfavorable favorability. Building the record never
    raises.
    """
    messages = [StubToolMessage(content=_serialize(label, style), name=REGIME_TOOL)]
    decision = {"action": action, "source": "declare_trade"}

    record = build_defensibility_record(messages, decision, mode="FIND")
    regime = record["regime"]

    # The regime label is usable, so the entry mirrors it (available is True).
    assert regime.get("available") is True

    should_oppose = label["favorability"] == "unfavorable" and action in ("BUY", "SELL")

    if should_oppose:
        # Statement present and a non-empty string (R7.4).
        statement = regime.get("trade_opposes_regime")
        assert isinstance(statement, str) and statement.strip(), (
            f"expected a non-empty opposition statement for action={action}, "
            f"favorability={label['favorability']}, got {statement!r}"
        )
        # It is surfaced in the human-readable summary too.
        assert statement in record["summary"]
    else:
        # Statement absent for HOLD or for favorable/neutral favorability (R7.4).
        assert "trade_opposes_regime" not in regime, (
            f"opposition statement must be absent for action={action}, "
            f"favorability={label['favorability']}, got {regime.get('trade_opposes_regime')!r}"
        )
