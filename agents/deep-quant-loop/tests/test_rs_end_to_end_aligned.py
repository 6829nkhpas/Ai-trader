"""End-to-end example test for an aligned FIND-mode run (task 14.2).

Feature: relative-strength-context

This is a deterministic, offline, EXAMPLE-based end-to-end test. It wires the
relative-strength feature through the three consumer layers it touches on a
committed decision WITHOUT a live LLM or the Rust Tool_Server:

  1. ``graph.build_defensibility_record`` (via ``_relative_strength_entry``)
     reads a usable ``aligned`` ``get_relative_strength`` result from message
     history and writes the defensibility relative-strength entry, mirroring the
     label verbatim (Req 8.1).
  2. ``stream_events.decision_events`` surfaces exactly one relative-strength
     ``VERIFICATION_STEP`` (check id ``relative-strength``) whose outcome is
     ``pass`` for an ``aligned`` label (Req 9.2), ordered before the ``DECISION``
     event (Req 9.6).
  3. ``journal.derive_setup_tags`` appends the fixed-position, low-cardinality
     relative-strength tag ``rs:leader-aligned`` (Req 10.1).

It also pins the prompt-level disclosure instruction (Req 7.4): the system
prompt tells the agent to disclose Index_Direction / Relative_Strength_State /
Alignment in its setup_validation. The core of the test, however, is the
data-flow above.

Validates: Requirements 7.4, 8.1, 9.2, 9.6, 10.1

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the consumer code reads — mirroring
``tests/test_rs_defensibility_mirror_properties.py``. The sys.path / import
pattern (service directory one level up prepended to ``sys.path``) matches the
other relative-strength tests so ``graph`` / ``stream_events`` / ``journal``
import when pytest runs from anywhere.
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
    _RS_MEASURE_FIELDS,
)
from stream_events import DECISION, VERIFICATION_STEP  # noqa: E402

RS_TOOL = "get_relative_strength"
RELATIVE_STRENGTH_CHECK_ID = "relative-strength"


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _aligned_rs_label():
    """A conforming, usable ``aligned`` Relative_Strength_Label.

    A leader in an up index, with the proposed BUY direction aligned with the
    index direction (index up + leader + aligned).
    """
    return {
        "index_direction": "up",
        "relative_strength_state": "leader",
        "alignment": "aligned",
        "measures": {
            "rs_ratio": 1.04,
            "rs_ratio_slope": 0.0021,
            "relative_return": 0.018,
            "correlation": 0.62,
            "beta": 1.15,
        },
        "benchmark": "NIFTY 50",
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "aligned_candles": 120,
    }


def _aligned_rs_message():
    """A single get_relative_strength ToolMessage carrying the aligned label."""
    return StubToolMessage(content=json.dumps(_aligned_rs_label()), name=RS_TOOL)


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
# End-to-end: an aligned FIND-mode run threads relative strength through all
# three consumer layers.
# ─────────────────────────────────────────────────────────────────────────────


def test_aligned_find_mode_run_threads_relative_strength_through_all_layers():
    """Validates: Requirements 8.1, 9.2, 9.6, 10.1

    A mocked ``aligned`` relative-strength result produces, end-to-end:
      * a defensibility relative-strength entry (available, mirroring the label),
      * exactly one ``pass`` relative-strength VERIFICATION_STEP (check
        ``relative-strength``) ordered before the ``DECISION`` event,
      * an ``rs:leader-aligned`` journal setup tag.
    """
    label = _aligned_rs_label()

    # ── Layer 1: defensibility record reads the aligned RS result (R8.1) ─────
    messages = [_aligned_rs_message()]
    decision = _committed_buy_decision()

    record = build_defensibility_record(messages, decision, mode="FIND")
    rs = record["relative_strength"]
    assert rs["available"] is True
    # The entry mirrors the label verbatim — no inference, no substitution.
    assert rs["index_direction"] == label["index_direction"] == "up"
    assert rs["relative_strength_state"] == label["relative_strength_state"] == "leader"
    assert rs["alignment"] == label["alignment"] == "aligned"
    assert rs["benchmark"] == label["benchmark"]
    assert set(rs["measures"].keys()) == set(_RS_MEASURE_FIELDS)
    for field in _RS_MEASURE_FIELDS:
        assert rs["measures"][field] == label["measures"][field]

    # Attach the record to the committed decision (as the live loop does).
    decision["defensibility"] = record

    # ── Layer 2: decision_events emits the RS step before DECISION ───────────
    events = list(stream_events.decision_events(decision))
    event_names = [name for name, _ in events]

    # Exactly one relative-strength VERIFICATION_STEP with the stable check id
    # and a ``pass`` outcome for the aligned label (R9.2).
    rs_steps = [
        (i, payload)
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == RELATIVE_STRENGTH_CHECK_ID
    ]
    assert len(rs_steps) == 1, f"expected exactly one relative-strength step, got {len(rs_steps)}"
    rs_step_index, rs_payload = rs_steps[0]
    assert rs_payload["outcome"] == "pass"

    # The relative-strength step precedes the DECISION event (R9.6).
    assert DECISION in event_names, "the run must emit a DECISION event"
    decision_index = event_names.index(DECISION)
    assert rs_step_index < decision_index

    # The committed decision is surfaced unchanged (BUY) — relative strength is a
    # defensibility surface, never a gate.
    assert events[decision_index][1]["action"] == "BUY"

    # ── Layer 3: journal setup fingerprint carries the RS tag (R10.1) ────────
    tags = journal.derive_setup_tags(decision)
    assert "rs:leader-aligned" in tags
    # Exactly one relative-strength tag, at its fixed position.
    rs_tags = [t for t in tags if t.startswith("rs:")]
    assert rs_tags == ["rs:leader-aligned"]
    # The forecaster appends exactly one ``fc:`` tag immediately after the
    # ``rs:`` tag; subsequent dimensions (tm/sess) and the multi-agent-debate
    # ``db:`` tag follow, so ``db:`` is the final tag. This decision carries no
    # forecast entry, so the tag immediately after ``rs:`` is ``fc:unknown``.
    rs_index = tags.index("rs:leader-aligned")
    assert tags[rs_index + 1] == "fc:unknown"
    assert tags[-1].startswith("db:")


# ── Req 7.4: prompt-level setup_validation disclosure instruction ────────────
def test_system_prompt_discloses_relative_strength_fields():
    """Validates: Requirements 7.4

    The disclosure is prompt-level: the system prompt instructs the agent to
    disclose the Index_Direction, Relative_Strength_State, and Alignment (taken
    from the get_relative_strength result) in its setup_validation.
    """
    sys_prompt = DEEP_QUANT_SYSTEM_PROMPT.lower()
    assert "index_direction" in sys_prompt
    assert "relative_strength_state" in sys_prompt
    assert "alignment" in sys_prompt
