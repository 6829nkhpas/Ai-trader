"""Property-based test for the non-blocking Regime Gate (graph.py, task 8.5).

Feature: regime-detection-gate

This module implements design **Property 32: The regime gate never modifies or
blocks a committed decision**:

    For any committed decision (action in {BUY, SELL, HOLD} with execution
    levels entry / stop_loss / take_profit / atr_14) and ANY regime present in
    message history — including an ``unfavorable`` regime against a directional
    trade — building the Defensibility_Record via ``build_defensibility_record``
    NEVER mutates the committed decision's action or execution levels, and NEVER
    blocks, overrides, or replaces the decision (it always returns a record, not
    ``None`` and not a block/replace signal).

Validates: Requirements 12.5, 12.6.

The Regime_Gate is a defensibility surface only: it may append an explicit
"trade opposes the regime assessment" statement to the record, but it must leave
the decision's action and execution levels exactly as committed.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record; reads (never writes) the committed decision.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == 'tool'`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads, mirroring
``tests/test_regime_defensibility_mirror_properties.py``.

The sys.path / import pattern mirrors the sibling regime tests: the service
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

import graph  # noqa: E402
from graph import build_defensibility_record  # noqa: E402

REGIME_TOOL = "get_market_regime"

# The committed-decision fields the gate must never modify (R12.5).
_PROTECTED_FIELDS = ("action", "entry", "stop_loss", "take_profit", "atr_14")


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
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_trend_state = st.sampled_from(["trending", "ranging", "transitional"])
_volatility_state = st.sampled_from(["low", "normal", "high"])
# Bias toward `unfavorable` (the case most likely to tempt a gate into blocking),
# while still exercising favorable / neutral.
_favorability = st.sampled_from(
    ["unfavorable", "unfavorable", "unfavorable", "favorable", "neutral"]
)
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

_price = st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False)
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _regime_label(draw):
    """Draw a conforming Regime_Label with arbitrary favorability."""
    return {
        "trend_state": draw(_trend_state),
        "volatility_state": draw(_volatility_state),
        "favorability": draw(_favorability),
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


@st.composite
def _decision(draw):
    """Draw a committed decision with an action and execution levels."""
    return {
        "action": draw(_action),
        "entry": draw(_price),
        "stop_loss": draw(_price),
        "take_profit": draw(_price),
        "atr_14": draw(_price),
        "source": "declare_trade",
        "execution_plan": draw(st.text(max_size=20)),
        "setup_validation": draw(st.text(max_size=20)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 32: the regime gate never modifies or blocks a committed decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 32
@settings(max_examples=200, deadline=None)
@given(
    decision=_decision(),
    regime=_regime_label(),
    style=_serialization_style,
)
def test_property_32_regime_gate_never_modifies_or_blocks_decision(decision, regime, style):
    """Validates: Requirements 12.5, 12.6

    Building the Defensibility_Record over ANY regime (especially an
    ``unfavorable`` one against a directional BUY/SELL trade) never mutates the
    committed decision's action or execution levels, and never blocks/replaces
    the decision (a record is always returned).
    """
    messages = [StubToolMessage(content=_serialize(regime, style), name=REGIME_TOOL)]

    # Snapshot the committed decision before the gate runs.
    snapshot = copy.deepcopy(decision)

    record = build_defensibility_record(messages, decision, mode="FIND")

    # ── The gate never blocks/replaces: a record is always returned (R12.6) ──
    assert record is not None
    assert isinstance(record, dict)
    # No block/replace signalling on the decision itself.
    assert "blocked" not in decision
    assert "block" not in decision
    assert "override" not in decision

    # ── The committed decision is unchanged in every protected field (R12.5) ─
    for field in _PROTECTED_FIELDS:
        assert decision[field] == snapshot[field], (
            f"regime gate mutated decision[{field!r}]: "
            f"{snapshot[field]!r} -> {decision[field]!r}"
        )
    # The decision dict as a whole is untouched (no fields added/removed/changed).
    assert decision == snapshot

    # ── The record faithfully reports the committed action / levels ──────────
    # (defensibility surfacing mirrors, never overrides, the committed decision).
    assert record["action"] == snapshot["action"]
    levels = record.get("levels")
    assert isinstance(levels, dict)
    assert levels["entry"] == snapshot["entry"]
    assert levels["stop_loss"] == snapshot["stop_loss"]
    assert levels["take_profit"] == snapshot["take_profit"]
