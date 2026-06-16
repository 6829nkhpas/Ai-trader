# Feature: session-expiry-awareness, Property 23: Per-session aggregation reports correct win-rate and expectancy
"""Property-based test for per-session journal aggregation (journal.py, task 7.3).

Feature: session-expiry-awareness

This module implements design **Property 23: Per-session aggregation reports
correct win-rate and expectancy**:

    For any set of recorded trades, grouping scored (win or loss) trades by the
    session-extended ``setup_key`` yields, for each group, a win-rate equal to
    the fraction of scored trades that are wins (within ``[0.0, 1.0]``) and an
    expectancy equal to the mean R-multiple of the group's scored trades, with
    any group holding fewer scored trades than the low-sample threshold flagged
    as a weak prior.

Validates: Requirements 10.4, 10.5.

The strategy generates a set of already-scored backtest trades whose only
varying defensibility dimension is the session entry (Session_Phase x
expiry-day flag), so each trade's ``setup_key`` differs only by its ``sess:``
tag and trades sharing a session bucket aggregate together. Each trade is
recorded via ``journal.record_backtest_trade`` with a win / loss / expired
status and (for win/loss) a finite R-multiple. The test then calls
``journal.get_stats`` and asserts that, for every per-setup (``by_setup``)
group, the reported ``win_rate`` and ``expectancy_r`` equal the independently
computed expected values and the ``low_sample`` flag matches the scored count
against the configured threshold.

DB ISOLATION: ``JOURNAL_DB_PATH`` is pointed at a throwaway temp file BEFORE
``journal`` is imported (and the module global is overridden defensively after
import), so the real ``trade_journal.db`` is never touched. The backtest source
is purged at the start of each generated example to keep examples independent.
The sys.path / import pattern mirrors the sibling ``test_session_*`` modules.
"""

import math
import os
import sys
import tempfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (journal.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

# Point the journal at a throwaway DB BEFORE importing it, so the module-level
# JOURNAL_DB_PATH global picks up the temp path and the real journal DB is
# untouched.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="sess_agg_journal_"), "trade_journal.db")
os.environ["JOURNAL_DB_PATH"] = _TMP_DB

import journal  # noqa: E402

# Defensive: ensure the module global points at the temp DB regardless of any
# import-time env caching.
journal.JOURNAL_DB_PATH = _TMP_DB

_SYMBOL = "SESSAGGTEST"
_TIMEFRAME = "15m"

# Phases the journal session tag recognises (each maps to a fixed bucket; some
# collapse together — pre_open/post_close -> offhours, expiry-day
# afternoon/closing -> expiry — which is exactly what we want to exercise
# grouping).
_PHASES = ["opening", "morning", "midday", "afternoon", "closing", "pre_open", "post_close"]

# Already-scored statuses only (no 'open' -> no candle fetch / network).
_STATUSES = ["win", "loss", "expired"]


def _make_decision(phase: str, is_expiry_day: bool) -> dict:
    """A BUY decision whose only varying dimension is the session entry.

    All other defensibility dimensions are absent (so their tags resolve to
    fixed unknown/neutral values), which means the resulting ``setup_key`` is
    fully determined by the ``sess:`` tag — trades sharing a session bucket
    aggregate into the same group.
    """
    return {
        "action": "BUY",
        "entry": 100.0,
        "stop_loss": 99.0,
        "take_profit": 103.0,
        "defensibility": {
            "session": {
                "session_phase": phase,
                "expiry_context": {"is_expiry_day": is_expiry_day, "days_until_expiry": 0},
                "minutes_since_open": 10.0,
                "minutes_until_close": 100.0,
                "time_favorability": "neutral",
            }
        },
    }


@st.composite
def _trade(draw):
    """A single already-scored trade spec: (phase, is_expiry_day, status, r)."""
    phase = draw(st.sampled_from(_PHASES))
    is_expiry_day = draw(st.booleans())
    status = draw(st.sampled_from(_STATUSES))
    r_multiple = draw(
        st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False)
    )
    return (phase, is_expiry_day, status, r_multiple)


def _expected_agg(items):
    """Independently compute (win_rate, expectancy_r, scored) for a group.

    Mirrors the definition under test: win-rate = wins / (wins + losses);
    expectancy = mean R-multiple over the scored (win/loss) trades; both rounded
    to 4 decimals; both None when there are no scored trades.
    """
    wins = sum(1 for s, _ in items if s == "win")
    losses = sum(1 for s, _ in items if s == "loss")
    scored = wins + losses
    win_rate = round(wins / scored, 4) if scored else None
    r_vals = [r for s, r in items if s in ("win", "loss") and math.isfinite(r)]
    expectancy_r = round(sum(r_vals) / len(r_vals), 4) if r_vals else None
    return win_rate, expectancy_r, scored


# ─────────────────────────────────────────────────────────────────────────────
# Property 23: Per-session aggregation reports correct win-rate and expectancy
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 23: Per-session aggregation reports correct win-rate and expectancy
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(trades=st.lists(_trade(), min_size=1, max_size=30))
def test_property_23_per_session_aggregation(trades):
    """Validates: Requirements 10.4, 10.5

    Recording a set of scored trades with varying session buckets and then
    aggregating via ``get_stats`` reports, per session-extended ``setup_key``,
    a win-rate equal to wins/(wins+losses), an expectancy equal to the mean
    R-multiple of the scored trades, and a low-sample flag matching the scored
    count against the configured threshold.
    """
    # Independence between generated examples: clear any prior backtest rows.
    journal.purge(source="backtest")

    # Record each trade and accumulate the independently-expected grouping by the
    # session-extended setup_key.
    expected: dict = {}
    for phase, is_expiry_day, status, r_multiple in trades:
        decision = _make_decision(phase, is_expiry_day)
        key = journal.setup_key_from_tags(journal.derive_setup_tags(decision))
        # win/loss carry a finite R-multiple; expired carries None (excluded
        # from win-rate/expectancy by definition).
        stored_r = r_multiple if status in ("win", "loss") else None
        row_id = journal.record_backtest_trade(
            decision, _SYMBOL, _TIMEFRAME, status, None, None, stored_r
        )
        assert row_id is not None, "record_backtest_trade should persist the trade"
        expected.setdefault(key, []).append((status, r_multiple))

    stats = journal.get_stats(symbol=_SYMBOL, source="backtest")
    assert not stats.get("unavailable"), f"get_stats unavailable: {stats.get('error')}"

    by_setup = {b["setup_key"]: b for b in stats["by_setup"]}

    # Every recorded (BUY) setup_key must appear as its own per-setup group, and
    # no spurious groups beyond those recorded.
    assert set(by_setup.keys()) == set(expected.keys()), (
        f"by_setup keys {set(by_setup.keys())} != expected {set(expected.keys())}"
    )

    threshold = journal.LOW_SAMPLE_THRESHOLD
    for key, items in expected.items():
        exp_win_rate, exp_expectancy, scored = _expected_agg(items)
        group = by_setup[key]

        # Win-rate: fraction of scored trades that are wins, within [0, 1] or None.
        assert group["win_rate"] == exp_win_rate, (
            f"win_rate for {key}: {group['win_rate']} != expected {exp_win_rate}"
        )
        if exp_win_rate is not None:
            assert 0.0 <= group["win_rate"] <= 1.0

        # Expectancy: mean R-multiple over the scored trades, or None.
        if exp_expectancy is None:
            assert group["expectancy_r"] is None, (
                f"expectancy_r for {key}: {group['expectancy_r']} != expected None"
            )
        else:
            assert group["expectancy_r"] is not None
            assert abs(group["expectancy_r"] - exp_expectancy) < 1e-9, (
                f"expectancy_r for {key}: {group['expectancy_r']} != expected {exp_expectancy}"
            )

        # Low-sample weak-prior flag (R10.5).
        assert group["low_sample"] == (scored < threshold), (
            f"low_sample for {key}: {group['low_sample']} != "
            f"expected {(scored < threshold)} (scored={scored}, threshold={threshold})"
        )
        # The group's scored count must match what we recorded.
        assert group["trades_scored"] == scored
