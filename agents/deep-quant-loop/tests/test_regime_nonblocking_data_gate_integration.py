"""Integration test for the non-blocking regime data gate (graph.py, task 14.1).

Feature: regime-detection-gate

A single ``get_market_regime`` result carrying an Unavailable_Marker
(``{"unavailable": true, "reason": ...}``) is, by itself, an *honest missing
optional input* — never usable directional data and never a forcing/blocking
signal. This integration-style test pins down that contract end-to-end across
the gate helpers in ``graph.py``:

  * ``_market_data_seen([unavailable_regime])`` is **False** — an unavailable
    regime does NOT satisfy the first-turn market-data acquisition gate, so it
    can never, on its own, make the loop believe usable data has arrived.
  * ``_market_data_attempted([unavailable_regime])`` is **True** — a market-data
    Analysis_Tool WAS called, so the regime is treated as a *sought-but-
    unavailable* input rather than a never-attempted one. This is exactly the
    non-blocking semantics of R4.4: the agent proceeds with remaining analysis
    and does not abort/fail solely because the regime is unavailable.
  * ``build_defensibility_record`` over that single message records the regime
    entry as ``available == False`` — an unavailable regime never fabricates or
    forces trend/volatility/favorability values.

Together these confirm that an unavailable regime alone neither satisfies the
data gate nor forces a committed decision (R4.4).

Validates: Requirements 4.4

The sys.path / import pattern mirrors ``tests/test_regime_market_data_gate_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``graph``
is importable when pytest is run from anywhere. The real LLM / Rust server is
never invoked — a lightweight stub ToolMessage (``type == "tool"`` with
``.name`` and ``.content``) stands in for the LangChain ``ToolMessage``, exactly
the shape the gate code reads.
"""

import json
import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    MARKET_DATA_TOOL_NAMES,
    _market_data_attempted,
    _market_data_seen,
    build_defensibility_record,
)

REGIME_TOOL = "get_market_regime"


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _unavailable_regime_message(reason="insufficient data: 18 valid candles received, 50 required"):
    """A single get_market_regime ToolMessage carrying an Unavailable_Marker."""
    payload = {"unavailable": True, "reason": reason, "symbol": "RELIANCE", "timeframe": "5m"}
    return StubToolMessage(content=json.dumps(payload), name=REGIME_TOOL)


# ─────────────────────────────────────────────────────────────────────────────
# R4.4: an unavailable regime is a non-blocking missing optional input.
# ─────────────────────────────────────────────────────────────────────────────


def test_unavailable_regime_does_not_satisfy_market_data_gate():
    """Validates: Requirements 4.4

    A lone unavailable regime result does NOT set ``market_data_seen`` — it is an
    honest missing input, not usable directional data.
    """
    # Precondition: the regime tool participates in the market-data gate at all.
    assert REGIME_TOOL in MARKET_DATA_TOOL_NAMES

    msg = _unavailable_regime_message()

    assert _market_data_seen([msg]) is False


def test_unavailable_regime_counts_as_a_market_data_attempt():
    """Validates: Requirements 4.4

    A lone unavailable regime result IS a market-data *attempt*: the tool was
    called but yielded no usable data, so the regime is treated as a sought-but-
    unavailable optional input (non-blocking), distinct from "never attempted".
    """
    msg = _unavailable_regime_message()

    assert _market_data_attempted([msg]) is True


def test_unavailable_regime_alone_does_not_force_a_committed_decision():
    """Validates: Requirements 4.4

    Combined gate read: a single unavailable regime ToolMessage neither satisfies
    the data gate (seen=False) nor, on its own, forces a decision — it is simply a
    missing optional input that was attempted (attempted=True). The gating logic
    therefore never fabricates "usable data has arrived" from an unavailable regime.
    """
    msg = _unavailable_regime_message()

    seen = _market_data_seen([msg])
    attempted = _market_data_attempted([msg])

    # Sought, but unavailable: attempted yet not usable.
    assert (seen, attempted) == (False, True)


def test_defensibility_record_marks_unavailable_regime_as_unavailable():
    """Validates: Requirements 4.4

    Building the defensibility record over the single unavailable regime message
    records the regime entry as unavailable (``available == False``) with NO
    fabricated trend/volatility/favorability — reinforcing that an unavailable
    regime never forces or fabricates anything.
    """
    msg = _unavailable_regime_message(reason="retrieval timeout")
    decision = {"action": "HOLD"}

    record = build_defensibility_record([msg], decision, mode="FIND")

    regime = record["regime"]
    assert regime["available"] is False
    # No fabricated categorical states leak into an unavailable entry.
    assert "trend_state" not in regime
    assert "volatility_state" not in regime
    assert "favorability" not in regime
    # The honest reason is carried through verbatim.
    assert regime["reason"] == "retrieval timeout"
    # The human-readable summary reports the regime as unavailable, not a value.
    assert "Regime: unavailable" in record["summary"]
