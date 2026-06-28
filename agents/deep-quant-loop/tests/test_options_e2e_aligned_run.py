"""End-to-end example test for an aligned FIND-mode options run (task 12.2).

Feature: options-agent-integration

This example-based test exercises the F3 pure-function pipeline end to end, WITHOUT
invoking the live LLM / graph / Rust server. Starting from a single usable
``get_options_analytics`` ToolMessage carrying a BULLISH + ALIGNED options label
and a committed BUY decision, it asserts the three downstream surfaces line up:

  1. ``graph.build_defensibility_record`` produces an ``options`` entry that is
     available and mirrors the tool label verbatim (R6.1).
  2. ``stream_events.decision_events`` emits exactly one options
     ``VERIFICATION_STEP`` with outcome ``pass`` (aligned -> pass, R7.2) ordered
     BEFORE the ``DECISION`` event (R7.4).
  3. ``journal.derive_setup_tags`` includes the ``opt:bullish-aligned`` tag at
     its fixed position (R8.1).

It also confirms the OI-wall / max-pain placement context (R5.4) survives into
the defensibility record verbatim.

Validates: Requirements 5.4, 6.1, 7.2, 7.4, 8.1.

The sys.path / import pattern mirrors the sibling ``test_options_*`` modules; a
lightweight stub ToolMessage (``type == "tool"`` with ``.name`` / ``.content``)
stands in for the LangChain ``ToolMessage`` the record builder reads.
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
from graph import build_defensibility_record  # noqa: E402
from stream_events import DECISION, VERIFICATION_STEP, decision_events  # noqa: E402

OPTIONS_TOOL = "get_options_analytics"


class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _aligned_label():
    """A usable, bullish + aligned Options_Bias_Label as the tool would emit."""
    return {
        "symbol": "BANKNIFTY",
        "underlying": "BANKNIFTY",
        "expiry": "2024-06-27",
        "spot": 48000.0,
        "pcr_oi": 1.45,
        "pcr_volume": 1.30,
        "max_pain": 48200.0,
        "oi_buildup": {"call": "short_buildup", "put": "long_buildup"},
        "oi_walls": {"support": 47800.0, "resistance": 48500.0},
        "iv_skew": {"put_minus_call": -0.8},
        "futures_basis": 12.5,
        "options_bias_state": "bullish",
        "alignment": "aligned",
        "chain_context": "own-chain",
    }


def _buy_decision():
    return {
        "action": "BUY",
        "source": "declare_trade",
        "conviction_score": 72,
        "setup_validation": "Aligned with bullish options positioning.",
        "execution_plan": "Long with stop below support.",
        "entry": 48050.0,
        "stop_loss": 47750.0,
        "take_profit": 48650.0,
    }


def test_e2e_aligned_find_mode_run():
    """Validates: Requirements 5.4, 6.1, 7.2, 7.4, 8.1

    A bullish+aligned options result flows into a defensibility ``options`` entry
    (R6.1, R5.4), a ``pass`` options verification step ordered before the DECISION
    (R7.2, R7.4), and an ``opt:bullish-aligned`` journal tag (R8.1).
    """
    label = _aligned_label()
    messages = [StubToolMessage(content=json.dumps(label), name=OPTIONS_TOOL)]
    decision = _buy_decision()

    # ── 1. Defensibility record: options entry available + mirrored (R6.1) ───
    record = build_defensibility_record(messages, decision, mode="FIND")
    opt = record["options"]
    assert opt.get("available") is True
    assert opt["options_bias_state"] == "bullish"
    assert opt["alignment"] == "aligned"
    assert opt["chain_context"] == "own-chain"
    # OI-wall / max-pain placement context carried verbatim (R5.4 surface).
    assert opt["max_pain"] == label["max_pain"]
    assert opt["oi_walls"] == label["oi_walls"]
    assert opt["pcr_oi"] == label["pcr_oi"]
    # An aligned trade is not an opposition, so no opposition statement is added.
    assert "trade_opposes_options" not in opt

    # Attach the record to the decision for the downstream surfaces (as the
    # finalize chokepoint would).
    decision_with_record = dict(decision)
    decision_with_record["defensibility"] = record

    # ── 2. Verification step: exactly one options 'pass' before DECISION ─────
    events = list(decision_events(decision_with_record))
    options_indices = [
        i for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == "options"
    ]
    decision_indices = [i for i, (name, _) in enumerate(events) if name == DECISION]

    assert len(options_indices) == 1, f"expected exactly one options step, got {events!r}"
    assert len(decision_indices) == 1, f"expected exactly one DECISION, got {events!r}"
    # aligned -> pass (R7.2)
    options_step = events[options_indices[0]][1]
    assert options_step["outcome"] == "pass"
    # ordered before the DECISION (R7.4)
    assert options_indices[0] < decision_indices[0]

    # ── 3. Journal tag: opt:bullish-aligned at its fixed position (R8.1) ─────
    tags = journal.derive_setup_tags(decision_with_record)
    assert "opt:bullish-aligned" in tags
    opt_tags = [t for t in tags if t.startswith("opt:")]
    assert opt_tags == ["opt:bullish-aligned"]
    # Fixed final position (after the db: tag).
    assert tags[-1] == "opt:bullish-aligned"
