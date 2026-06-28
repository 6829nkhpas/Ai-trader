"""Property-based test for per-options-bias journal aggregation
(journal.py, task 11.3).

Feature: options-agent-integration

This module implements live per-options-bias aggregation:

    Grouping scored (win/loss) trades by the options-extended ``setup_key``
    reports, for each group, a win-rate equal to the fraction of that group's
    scored trades that are wins (a value in [0.0, 1.0]) and an expectancy equal
    to the mean R-multiple of that group's scored trades; groups holding fewer
    scored trades than ``LOW_SAMPLE_THRESHOLD`` are flagged as a weak prior.
    This confirms the feature's lift is validated LIVE through the Trade_Journal
    — no historical option-chain backtest is required.

Validates: Requirements 8.4, 8.5.

The implementation under test lives in ``journal.py``:
  - ``derive_setup_tags`` / ``setup_key_from_tags`` — build the options-extended
    ``setup_key`` (the ``opt:<value>`` dimension is appended at a fixed position
    by ``_options_tag``).
  - ``_aggregate(rows)`` — counts wins/losses and computes
    ``win_rate = wins / (wins + losses)`` and ``expectancy_r = mean R-multiple``.
  - ``get_stats(...)`` — the real aggregation entry point. It groups the
    directional (BUY/SELL) trades by ``setup_key`` into ``by_setup`` and
    surfaces ``low_sample_threshold`` plus an overall ``low_sample`` flag. A
    group is flagged a weak prior when its ``trades_scored`` is below
    ``low_sample_threshold``.

No live LLM / Rust server is involved. Scored trades are seeded through the
real ``record_backtest_trade`` public path into a TEMP sqlite DB (so no real
journal is touched); only already-resolved statuses are inserted, so
``get_stats`` -> ``score_open_trades`` finds no ``open`` rows and performs no
candle fetch. The temp DB is removed on teardown.

The sys.path / import pattern and the temp-DB seed harness mirror
``test_rs_aggregation_properties.py`` exactly.
"""

import atexit
import math
import os
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
_ORIG_LOW_SAMPLE = journal.LOW_SAMPLE_THRESHOLD
_fd, _TMP_DB = tempfile.mkstemp(prefix="opt_agg_journal_", suffix=".db")
os.close(_fd)
journal.JOURNAL_DB_PATH = _TMP_DB


@atexit.register
def _cleanup():
    journal.JOURNAL_DB_PATH = _ORIG_DB_PATH
    journal.LOW_SAMPLE_THRESHOLD = _ORIG_LOW_SAMPLE
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass


# ── Strategies ───────────────────────────────────────────────────────────────
# The fixed options tag values (R8.3) each mapped to an options defensibility
# entry that ``journal._options_tag`` collapses back to exactly that tag value.
_OPT_VALUE_TO_ENTRY = {
    "bullish-aligned": {"options_bias_state": "bullish", "alignment": "aligned", "available": True},
    "bullish-misaligned": {"options_bias_state": "bullish", "alignment": "misaligned", "available": True},
    "bearish-aligned": {"options_bias_state": "bearish", "alignment": "aligned", "available": True},
    "bearish-misaligned": {"options_bias_state": "bearish", "alignment": "misaligned", "available": True},
    # A neutral bias collapses to ``neutral`` regardless of Alignment.
    "neutral": {"options_bias_state": "neutral", "alignment": "neutral", "available": True},
    # An unavailable options entry collapses to ``unknown``.
    "unknown": {"available": False},
}

_opt_value = st.sampled_from(sorted(_OPT_VALUE_TO_ENTRY.keys()))
_direction = st.sampled_from(["BUY", "SELL"])
# R-multiples drawn from exactly-representable binary fractions so the mean is
# independent of summation order and matches the journal's rounded value.
_r_multiple = st.sampled_from([-2.0, -1.0, -0.5, 0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0])
# Outcome status: only resolved statuses (no 'open', which would trigger scoring
# and a candle fetch). 'win'/'loss' are scored; 'expired' is excluded from scored.
_status = st.sampled_from(["win", "loss", "expired"])


@st.composite
def _trade_spec(draw):
    return {
        "opt_value": draw(_opt_value),
        "direction": draw(_direction),
        "status": draw(_status),
        "r_multiple": draw(_r_multiple),
    }


def _make_decision(opt_value, direction):
    """Build a committed-decision dict whose options bias collapses to
    ``opt_value``."""
    return {
        "action": direction,
        "defensibility": {"options": dict(_OPT_VALUE_TO_ENTRY[opt_value])},
    }


def _approx_eq(a, b):
    if a is None or b is None:
        return a is b or a == b
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# Feature: options-agent-integration — per-options-bias aggregation reports
# correct win-rate and expectancy, with weak-prior flagging (live validation,
# no historical option-chain backtest required).
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    specs=st.lists(_trade_spec(), min_size=1, max_size=40),
    threshold=st.integers(min_value=1, max_value=25),
)
def test_per_options_bias_aggregation_metrics(specs, threshold):
    """Validates: Requirements 8.4, 8.5

    Seed scored (and some excluded) trades grouped under options-extended
    ``setup_key``s, then run the real aggregation and assert per group:
      * win-rate == wins / scored and lies in [0.0, 1.0]   (R8.4)
      * expectancy == mean R-multiple of the group's scored trades  (R8.4)
      * a group with fewer scored trades than LOW_SAMPLE_THRESHOLD is a weak
        prior (its trades_scored < surfaced low_sample_threshold)    (R8.5)
    Distinct ``opt:`` components keep distinct option-bias setups in separate
    groups, so the lift can be measured live per options bias.
    """
    # Clean slate + a known low-sample threshold for this example.
    journal.purge()
    journal.LOW_SAMPLE_THRESHOLD = threshold

    # ── Seed trades through the real public path; build expectations ─────────
    # expected[key] = {"win": n, "loss": n, "scored_rs": [r, ...]}
    expected: dict = {}
    for spec in specs:
        decision = _make_decision(spec["opt_value"], spec["direction"])
        key = journal.setup_key_from_tags(journal.derive_setup_tags(decision))
        status = spec["status"]
        r = spec["r_multiple"] if status in ("win", "loss") else None
        row_id = journal.record_backtest_trade(
            decision, symbol="TEST", timeframe="1d", status=status,
            outcome_price=100.0, outcome_at=1.0, r_multiple=r,
        )
        assert row_id is not None, "seeding a backtest trade must succeed"

        agg = expected.setdefault(key, {"win": 0, "loss": 0, "scored_rs": []})
        if status == "win":
            agg["win"] += 1
            agg["scored_rs"].append(r)
        elif status == "loss":
            agg["loss"] += 1
            agg["scored_rs"].append(r)

    # ── Run the real aggregation ─────────────────────────────────────────────
    stats = journal.get_stats()

    assert stats.get("low_sample_threshold") == threshold
    by_setup = {b["setup_key"]: b for b in stats["by_setup"]}

    # Every seeded options-extended setup_key is reported exactly once, and each
    # setup_key carries a distinct ``opt:`` component.
    assert set(by_setup.keys()) == set(expected.keys())
    for key in by_setup:
        assert any(part.startswith("opt:") for part in key.split("|")), (
            f"setup_key {key!r} must carry an opt: component"
        )

    total_scored = 0
    for key, exp in expected.items():
        group = by_setup[key]
        wins, losses = exp["win"], exp["loss"]
        scored = wins + losses
        total_scored += scored

        assert group["wins"] == wins
        assert group["losses"] == losses
        assert group["trades_scored"] == scored

        # ── Win-rate: fraction of scored trades that are wins, in [0, 1] (R8.4)
        if scored == 0:
            assert group["win_rate"] is None
            assert group["expectancy_r"] is None
        else:
            expected_wr = round(wins / scored, 4)
            assert _approx_eq(group["win_rate"], expected_wr)
            assert 0.0 <= group["win_rate"] <= 1.0

            # ── Expectancy: mean R-multiple of the group's scored trades (R8.4)
            expected_exp = round(sum(exp["scored_rs"]) / len(exp["scored_rs"]), 4)
            assert _approx_eq(group["expectancy_r"], expected_exp)

        # ── Weak-prior flagging (R8.5): a group with fewer scored trades than
        # the low-sample threshold is flagged a weak prior.
        is_weak_prior = group["trades_scored"] < stats["low_sample_threshold"]
        assert is_weak_prior == (scored < threshold)

    # The overall weak-prior flag is the explicit boolean the journal exposes.
    assert stats["low_sample"] == (total_scored < threshold)
