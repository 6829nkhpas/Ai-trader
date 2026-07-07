"""End-to-end example test for an aligned FIND-mode run (task 16.2).

Feature: volatility-aware-forecaster

This is a deterministic, offline, EXAMPLE-based end-to-end test. It stitches the
volatility-aware-forecaster feature through the four consumer layers it touches
on a committed decision WITHOUT a live LLM or the Rust Tool_Server:

  1. ``graph.build_defensibility_record`` (via ``_forecast_entry``) reads a
     usable ``aligned`` ``get_forecast`` Forecast_Label from message history and
     writes the defensibility forecast entry, mirroring the label verbatim and
     surfacing the disclosure fields Projected_Direction / Up_Probability /
     Expected_Move_ATR / Forecast_Alignment (Req 8.4, 9.1).
  2. ``stream_events.decision_events`` surfaces exactly one forecast
     ``VERIFICATION_STEP`` (check id ``forecast``) whose outcome is ``pass`` for
     an ``aligned`` label (Req 10.2), ordered before the ``DECISION`` event
     (Req 10.6).
  3. ``journal.derive_setup_tags`` appends the fixed-position, low-cardinality
     forecast tag ``fc:aligned-strong`` (strong because the Up_Probability 0.72
     sits >= 0.15 from a 0.5 coin flip) (Req 11.1).
  4. ``journal.record_decision`` persists the forecast's Up_Probability into the
     nullable ``forecast_up_probability`` column, which round-trips to 0.72
     (Req 11.4).

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the consumer code reads — mirroring
``tests/test_forecast_defensibility_mirror_properties.py`` and
``tests/test_rs_end_to_end_aligned.py``. ``record_decision`` only INSERTs the
row (scoring / candle fetch is a separate ``get_stats`` path), so no network is
touched; the persisted value is read straight back via sqlite from a TEMP DB
that is removed on teardown.

The sys.path / import pattern (service directory one level up prepended to
``sys.path``) matches the other forecaster tests so ``graph`` / ``stream_events``
/ ``journal`` import when pytest runs from anywhere.

Validates: Requirements 8.4, 9.1, 10.2, 10.6, 11.1, 11.4
"""

import json
import os
import sqlite3
import sys
import tempfile

# Make the service package importable (graph.py / stream_events.py / journal.py
# live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
import stream_events  # noqa: E402
from graph import (  # noqa: E402
    build_defensibility_record,
    _FORECAST_MEASURE_FIELDS,
)
from stream_events import DECISION, VERIFICATION_STEP  # noqa: E402

FORECAST_TOOL = "get_forecast"
FORECAST_CHECK_ID = "forecast"

# The aligned label's Up_Probability. abs(0.72 - 0.5) = 0.22 >= 0.15 split, so
# the journal forecast tag's confidence band is ``strong`` (Req 11.1).
ALIGNED_UP_PROBABILITY = 0.72


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _aligned_forecast_label():
    """A conforming, usable ``aligned`` Forecast_Label with a strong probability.

    Projected_Direction ``up`` (>= 0.5 probability, R3.5) aligned with the
    proposed BUY direction, with a strong Up_Probability of 0.72.
    """
    return {
        "projected_direction": "up",
        "up_probability": ALIGNED_UP_PROBABILITY,
        "expected_move_atr": 0.85,
        "forecast_confidence": 0.44,
        "forecast_alignment": "aligned",
        "measures": {
            "drift": 0.0021,
            "volatility": 0.0095,
            "standardized_drift": 0.44,
            "atr": 1.32,
        },
        "regime_trend_state": "trending",
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "candles_used": 120,
    }


def _aligned_forecast_message():
    """A single get_forecast ToolMessage carrying the aligned label."""
    return StubToolMessage(content=json.dumps(_aligned_forecast_label()), name=FORECAST_TOOL)


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
# End-to-end: an aligned FIND-mode run threads the forecast through all four
# consumer layers.
# ─────────────────────────────────────────────────────────────────────────────


def test_aligned_find_mode_run_threads_forecast_through_all_layers():
    """Validates: Requirements 8.4, 9.1, 10.2, 10.6, 11.1, 11.4

    A mocked ``aligned`` forecast result produces, end-to-end:
      * a defensibility forecast entry (available, mirroring the label, with the
        Projected_Direction / Up_Probability / Expected_Move_ATR /
        Forecast_Alignment disclosure fields present),
      * exactly one ``pass`` forecast VERIFICATION_STEP (check ``forecast``)
        ordered before the ``DECISION`` event,
      * an ``fc:aligned-strong`` journal setup tag,
      * a persisted ``forecast_up_probability`` of 0.72.
    """
    label = _aligned_forecast_label()

    # ── Layer 1: defensibility record reads the aligned forecast (R9.1) ──────
    messages = [_aligned_forecast_message()]
    decision = _committed_buy_decision()

    record = build_defensibility_record(messages, decision, mode="FIND")
    forecast = record["forecast"]
    assert forecast["available"] is True
    # The entry mirrors the label verbatim — no inference, no substitution.
    assert forecast["forecast_alignment"] == label["forecast_alignment"] == "aligned"
    assert forecast["projected_direction"] == label["projected_direction"] == "up"
    assert forecast["up_probability"] == label["up_probability"] == ALIGNED_UP_PROBABILITY
    # R8.4 disclosure fields are present on the entry.
    assert forecast["expected_move_atr"] == label["expected_move_atr"]
    assert forecast["forecast_confidence"] == label["forecast_confidence"]
    # The named Forecast_Measures are mirrored verbatim.
    measures = forecast["measures"]
    assert set(measures.keys()) == set(_FORECAST_MEASURE_FIELDS)
    for field in _FORECAST_MEASURE_FIELDS:
        assert measures[field] == label["measures"][field]

    # Attach the record to the committed decision (as the live loop does).
    decision["defensibility"] = record

    # ── Layer 2: decision_events emits the forecast step before DECISION ─────
    events = list(stream_events.decision_events(decision))
    event_names = [name for name, _ in events]

    # Exactly one forecast VERIFICATION_STEP with the stable check id and a
    # ``pass`` outcome for the aligned label (R10.2).
    forecast_steps = [
        (i, payload)
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == FORECAST_CHECK_ID
    ]
    assert len(forecast_steps) == 1, (
        f"expected exactly one forecast step, got {len(forecast_steps)}"
    )
    forecast_step_index, forecast_payload = forecast_steps[0]
    assert forecast_payload["outcome"] == "pass"

    # The forecast step precedes the DECISION event (R10.6).
    assert DECISION in event_names, "the run must emit a DECISION event"
    decision_index = event_names.index(DECISION)
    assert forecast_step_index < decision_index

    # The committed decision is surfaced unchanged (BUY) — the forecast is a
    # defensibility surface, never a gate.
    assert events[decision_index][1]["action"] == "BUY"

    # ── Layer 3: journal setup fingerprint carries the forecast tag (R11.1) ──
    tags = journal.derive_setup_tags(decision)
    assert "fc:aligned-strong" in tags
    # Exactly one forecast tag, at its fixed position; later dimensions (tm/sess/
    # db/opt) and the opportunity-engine ``tier:`` tag follow, so ``tier:`` is the
    # final tag (adaptive-opportunity-engine R9.2).
    fc_tags = [t for t in tags if t.startswith("fc:")]
    assert fc_tags == ["fc:aligned-strong"]
    assert tags[-1].startswith("tier:")

    # ── Layer 4: the forecast Up_Probability is persisted (R11.4) ────────────
    _orig_db_path = journal.JOURNAL_DB_PATH
    fd, tmp_db = tempfile.mkstemp(prefix="fc_aligned_e2e_journal_", suffix=".db")
    os.close(fd)
    journal.JOURNAL_DB_PATH = tmp_db
    try:
        row_id = journal.record_decision(
            decision, symbol="RELIANCE", timeframe="15m", mode="FIND"
        )
        assert row_id is not None, "recording the decision must succeed"

        conn = sqlite3.connect(journal.JOURNAL_DB_PATH, timeout=10.0)
        try:
            cur = conn.execute(
                "SELECT forecast_up_probability FROM trades WHERE id=?", (row_id,)
            )
            row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None, "the recorded row must exist"
        assert row[0] == ALIGNED_UP_PROBABILITY
    finally:
        journal.JOURNAL_DB_PATH = _orig_db_path
        try:
            os.remove(tmp_db)
        except OSError:
            pass
