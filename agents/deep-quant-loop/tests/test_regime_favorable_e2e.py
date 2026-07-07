"""End-to-end example test for a favorable FIND-mode run (task 14.2).

Feature: regime-detection-gate

This is a deterministic, offline, EXAMPLE-based end-to-end test. It wires the
regime feature through the three consumer layers it touches on a committed
decision WITHOUT a live LLM or the Rust Tool_Server:

  1. ``graph.build_defensibility_record`` reads a favorable ``get_market_regime``
     result from message history and writes the defensibility regime entry
     (Req 7.1).
  2. ``stream_events.decision_events`` surfaces exactly one regime
     ``VERIFICATION_STEP`` (check id ``market-regime``) whose outcome is ``pass``
     for a favorable regime (Req 8.2), ordered before the ``DECISION`` event
     (Req 8.6).
  3. ``journal.derive_setup_tags`` appends the fixed-position, low-cardinality
     regime tag ``regime:trend-favorable`` (Req 9.1).

It also pins the prompt-level disclosure instruction (Req 6.4): the system
prompt tells the agent to state trend_state / volatility_state / favorability in
its setup_validation. The core of the test, however, is the data-flow above.

Validates: Requirements 6.4, 7.1, 8.2, 8.6, 9.1

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the consumer code reads — mirroring
``tests/test_regime_defensibility_mirror_properties.py``. The sys.path / import
pattern (service directory one level up prepended to ``sys.path``) matches the
other regime tests so ``graph`` / ``stream_events`` / ``journal`` import when
pytest runs from anywhere.
"""

import json
import os
import sys

# Make the service package importable (graph.py / stream_events.py / journal.py
# live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
import stream_events  # noqa: E402
from graph import (  # noqa: E402
    DEEP_QUANT_SYSTEM_PROMPT,
    build_defensibility_record,
)
from stream_events import DECISION, VERIFICATION_STEP

REGIME_TOOL = "get_market_regime"


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _favorable_regime_label():
    """A conforming, FAVORABLE Regime_Label (trending + normal volatility)."""
    return {
        "trend_state": "trending",
        "volatility_state": "normal",
        "favorability": "favorable",
        "measures": {
            "directional_strength": 31.2,
            "choppiness": 44.7,
            "efficiency_ratio": 0.38,
            "atr_percentile": 62.0,
            "bb_width": 0.041,
        },
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "candles_used": 120,
    }


def _favorable_regime_message():
    """A single get_market_regime ToolMessage carrying the favorable label."""
    return StubToolMessage(content=json.dumps(_favorable_regime_label()), name=REGIME_TOOL)


def _committed_buy_decision():
    """A committed directional (BUY) decision with execution levels."""
    return {
        "action": "BUY",
        "conviction_score": 7,
        "source": "declare_trade",
        "entry": 100.0,
        "stop_loss": 96.0,
        "take_profit": 110.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: favorable FIND-mode run threads through all three consumer layers.
# ─────────────────────────────────────────────────────────────────────────────


def test_favorable_find_mode_run_threads_regime_through_all_layers():
    """Validates: Requirements 7.1, 8.2, 8.6, 9.1

    A mocked favorable regime produces, end-to-end:
      * a defensibility regime entry (available, favorability == favorable),
      * exactly one ``pass`` regime VERIFICATION_STEP (check ``market-regime``)
        ordered before the ``DECISION`` event,
      * a ``regime:trend-favorable`` journal setup tag.
    """
    # ── Layer 1: defensibility record reads the favorable regime (R7.1) ──────
    messages = [_favorable_regime_message()]
    decision = _committed_buy_decision()

    record = build_defensibility_record(messages, decision, mode="FIND")
    regime = record["regime"]
    assert regime["available"] is True
    assert regime["favorability"] == "favorable"
    assert regime["trend_state"] == "trending"
    assert regime["volatility_state"] == "normal"

    # Attach the record to the committed decision (as the live loop does).
    decision["defensibility"] = record

    # ── Layer 2: decision_events emits the regime step before DECISION ───────
    events = list(stream_events.decision_events(decision))
    event_names = [name for name, _ in events]

    # Exactly one regime VERIFICATION_STEP with the stable check id and a pass
    # outcome (R8.2).
    regime_steps = [
        (name, payload)
        for name, payload in events
        if name == VERIFICATION_STEP and payload.get("check") == "market-regime"
    ]
    assert len(regime_steps) == 1, f"expected exactly one regime step, got {len(regime_steps)}"
    assert regime_steps[0][1]["outcome"] == "pass"

    # The regime step precedes the DECISION event (R8.6).
    assert DECISION in event_names, "the run must emit a DECISION event"
    regime_step_index = next(
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == "market-regime"
    )
    decision_index = event_names.index(DECISION)
    assert regime_step_index < decision_index

    # The committed decision is surfaced unchanged (BUY) — the regime is a
    # defensibility surface, never a gate.
    decision_payload = events[decision_index][1]
    assert decision_payload["action"] == "BUY"

    # ── Layer 3: journal setup fingerprint carries the regime tag (R9.1) ─────
    tags = journal.derive_setup_tags(decision)
    assert "regime:trend-favorable" in tags
    # Exactly one regime tag, at its fixed position (low-cardinality, R9.1). The
    # relative-strength dimension (relative-strength-context) appends exactly one
    # rs: tag immediately AFTER the regime tag, and the volatility-aware-forecaster
    # dimension appends exactly one fc: tag immediately AFTER the rs: tag. The
    # deterministic order is ``... va: regime: rs: fc: tm: sess: db:`` — the
    # multi-agent-debate ``db:`` tag is the final tag. The regime tag is
    # immediately followed by the rs: tag, which is immediately followed by the
    # fc: tag.
    regime_tags = [t for t in tags if t.startswith("regime:")]
    assert regime_tags == ["regime:trend-favorable"]
    rs_tags = [t for t in tags if t.startswith("rs:")]
    assert len(rs_tags) == 1
    fc_tags = [t for t in tags if t.startswith("fc:")]
    assert len(fc_tags) == 1
    regime_index = tags.index("regime:trend-favorable")
    assert tags[regime_index + 1] == rs_tags[0]
    assert tags[regime_index + 2] == fc_tags[0]
    assert tags[-1].startswith("tier:")  # tier: is the final dimension (opportunity engine R9.2)


# ── Req 6.4: prompt-level setup_validation disclosure instruction ────────────
def test_system_prompt_discloses_regime_fields():
    """Validates: Requirements 6.4

    The disclosure is prompt-level: the system prompt instructs the agent to
    state the Trend_State, Volatility_State, and Favorability (taken from the
    get_market_regime result) in its setup_validation.
    """
    sys_prompt = DEEP_QUANT_SYSTEM_PROMPT.lower()
    assert "trend_state" in sys_prompt
    assert "volatility_state" in sys_prompt
    assert "favorability" in sys_prompt
