"""Property-based test for forecast Up_Probability persistence
(journal.py, task 12.4).

Feature: volatility-aware-forecaster

This module implements design **Property 29: The forecast Up_Probability
round-trips through persistence**:

    Recording a committed decision (or an already-scored backtest trade) whose
    defensibility forecast entry carries a finite Up_Probability in [0.0, 1.0]
    persists that exact value into the nullable ``forecast_up_probability REAL``
    column and reads it back unchanged; recording a decision whose forecast
    entry is unavailable (``available`` is False) or missing persists NULL.

Validates: Requirements 11.4.

The implementation under test lives in ``journal.py``:
  - ``_forecast_up_probability(deff)`` — extracts the forecast ``up_probability``
    for persistence (a finite number) or ``None`` when the forecast entry is
    absent / explicitly unavailable / non-finite.
  - ``record_decision(...)`` and ``record_backtest_trade(...)`` — both read
    ``decision['defensibility']['forecast']['up_probability']`` and persist it
    into the ``forecast_up_probability`` column (NULL when unavailable).
  - ``_init_db`` / ``_ensure_column`` — add the nullable column via a guarded
    ``ALTER TABLE`` so existing journals upgrade in place.

No live LLM / Rust server is involved. Decisions are persisted through the real
public record paths into a TEMP sqlite DB (so no real journal is touched) and
the persisted value is read straight back via sqlite. ``record_decision`` stores
non-scoreable rows as ``hold`` and ``record_backtest_trade`` stores
already-resolved rows, so no ``open`` rows exist and ``get_stats`` is never
invoked — there is no candle fetch. The temp DB is removed on teardown.

The sys.path / import pattern and the temp-DB harness mirror
``test_forecast_aggregation_properties.py`` exactly.
"""

import atexit
import math
import os
import sqlite3
import sys
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (journal.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402


# ── Temp DB isolation ────────────────────────────────────────────────────────
# Point the journal at a throwaway sqlite file for the whole module so no real
# journal is touched. Each example purges the table to start from a clean slate.
_ORIG_DB_PATH = journal.JOURNAL_DB_PATH
_fd, _TMP_DB = tempfile.mkstemp(prefix="fc_prob_persist_journal_", suffix=".db")
os.close(_fd)
journal.JOURNAL_DB_PATH = _TMP_DB


@atexit.register
def _cleanup():
    journal.JOURNAL_DB_PATH = _ORIG_DB_PATH
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass


def _read_persisted_probability(row_id):
    """Read the ``forecast_up_probability`` column for ``row_id`` straight from
    sqlite (None when the column is NULL)."""
    conn = sqlite3.connect(journal.JOURNAL_DB_PATH, timeout=10.0)
    try:
        cur = conn.execute(
            "SELECT forecast_up_probability FROM trades WHERE id=?", (row_id,)
        )
        row = cur.fetchone()
        assert row is not None, "the recorded row must exist"
        return row[0]
    finally:
        conn.close()


# ── Strategies ───────────────────────────────────────────────────────────────
_action = st.sampled_from(["BUY", "SELL", "HOLD"])
_record_path = st.sampled_from(["decision", "backtest"])

# Available forecast: a finite Up_Probability in [0.0, 1.0] (R11.4 round-trip).
_up_probability = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _available_case(draw):
    """A decision whose forecast entry carries a finite Up_Probability in
    [0, 1]; the expected persisted value is that exact probability."""
    p = draw(_up_probability)
    decision = {
        "action": draw(_action),
        "defensibility": {
            "forecast": {
                "available": True,
                "forecast_alignment": draw(st.sampled_from(["aligned", "misaligned", "neutral"])),
                "up_probability": p,
            }
        },
    }
    return decision, p


# Unavailable / missing forecast variants whose expected persisted value is NULL
# (None). Each must collapse to None via ``_forecast_up_probability`` (R11.4).
_UNAVAILABLE_FORECASTS = [
    {"available": False},                                   # explicitly unavailable
    {"available": False, "reason": "insufficient data"},    # unavailable marker
    {"available": True, "forecast_alignment": "neutral"},   # available but no probability
]


@st.composite
def _unavailable_case(draw):
    """A decision whose forecast entry is unavailable or missing the probability;
    the expected persisted value is NULL (None)."""
    kind = draw(st.sampled_from(["unavailable", "no_forecast", "empty_defensibility", "forecast_none"]))
    if kind == "unavailable":
        defensibility = {"forecast": draw(st.sampled_from(_UNAVAILABLE_FORECASTS))}
    elif kind == "no_forecast":
        defensibility = {"regime": {"available": False}}    # other entries, no forecast key
    elif kind == "empty_defensibility":
        defensibility = {}
    else:  # forecast_none
        defensibility = {"forecast": None}
    decision = {"action": draw(_action), "defensibility": defensibility}
    return decision, None


@st.composite
def _case(draw):
    """Either an available (finite probability) or an unavailable/missing case,
    plus the record path to exercise (live decision vs backtest trade)."""
    available = draw(st.booleans())
    decision, expected = draw(_available_case() if available else _unavailable_case())
    return decision, expected, draw(_record_path)


def _record(decision, path):
    """Persist ``decision`` through the requested real public path; return the
    new row id."""
    if path == "decision":
        return journal.record_decision(decision, symbol="TEST", timeframe="1d", mode="FIND")
    return journal.record_backtest_trade(
        decision, symbol="TEST", timeframe="1d", status="win",
        outcome_price=100.0, outcome_at=1.0, r_multiple=1.0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Feature: volatility-aware-forecaster, Property 29: The forecast Up_Probability
# round-trips through persistence
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=200, deadline=None)
@given(case=_case())
def test_property_29_up_probability_round_trips_through_persistence(case):
    """Validates: Requirements 11.4

    Persist a decision through the real record path and read the persisted
    ``forecast_up_probability`` straight back from sqlite:
      * available case  -> the exact finite Up_Probability in [0, 1] round-trips
      * unavailable/missing case -> the column is NULL (None), never fabricated
    """
    decision, expected, path = case

    # Clean slate so the row we read back is unambiguous.
    journal.purge()

    row_id = _record(decision, path)
    assert row_id is not None, "recording the decision must succeed"

    persisted = _read_persisted_probability(row_id)

    if expected is None:
        # Unavailable / missing forecast -> NULL, no fabricated value (R11.4).
        assert persisted is None
    else:
        # Available forecast -> the finite probability round-trips unchanged.
        assert persisted is not None
        assert math.isclose(persisted, expected, rel_tol=0.0, abs_tol=1e-9)
        assert 0.0 <= persisted <= 1.0
