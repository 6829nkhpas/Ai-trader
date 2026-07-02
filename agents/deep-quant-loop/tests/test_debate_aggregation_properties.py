# Feature: multi-agent-debate, Property 25: Aggregation groups by the debate-extended setup key
"""Property-based test for debate-extended journal aggregation (journal.py, task 13.3).

Feature: multi-agent-debate

This module implements design **Property 25: Aggregation groups by the
debate-extended setup key**:

    Aggregation groups scored trades by the debate-extended ``setup_key`` (which
    now includes the ``db:<consensus>`` dimension), reporting each group's
    win-rate and expectancy and flagging groups below the configured low-sample
    threshold as a weak prior. Two decisions that differ ONLY in their debate
    consensus produce DIFFERENT ``setup_key`` values, so they aggregate into
    separate groups.

Validates: Requirements 9.5.

Two complementary layers are exercised, both fully hermetic (no LLM / Rust
server / network):

  1. PURE grouping (``derive_setup_tags`` / ``setup_key_from_tags``): the derived
     ``setup_key`` always carries exactly one ``db:`` dimension at a FIXED final
     position, is deterministic for identical inputs, and two decisions that
     differ ONLY in their debate consensus yield DIFFERENT ``setup_key`` values
     — proving the debate dimension participates in grouping.

  2. ``get_stats`` AGGREGATION: a set of already-scored backtest trades whose
     only varying defensibility dimension is the debate consensus is recorded via
     the real ``record_backtest_trade`` public path; ``get_stats`` then reports,
     per debate-extended ``setup_key``, a win-rate equal to wins/(wins+losses),
     an expectancy equal to the mean R-multiple of the scored trades, and a
     low-sample flag matching the scored count against the configured threshold.
     Decisions differing only in consensus form SEPARATE ``by_setup`` groups.

DB ISOLATION: ``JOURNAL_DB_PATH`` is pointed at a throwaway temp file BEFORE
``journal`` is imported (and the module global is overridden defensively after
import), so the real ``trade_journal.db`` is never touched. Only already-resolved
statuses (win/loss/expired) are inserted, so ``get_stats`` -> ``score_open_trades``
finds no ``open`` rows and performs no candle fetch; as a belt-and-suspenders
guard ``score_open_trades`` is also monkeypatched to a no-op. The backtest source
is purged at the start of each generated example to keep examples independent.
The sys.path / import pattern mirrors the sibling aggregation-property modules.
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
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="debate_agg_journal_"), "trade_journal.db")
os.environ["JOURNAL_DB_PATH"] = _TMP_DB

import journal  # noqa: E402

# Defensive: ensure the module global points at the temp DB regardless of any
# import-time env caching.
journal.JOURNAL_DB_PATH = _TMP_DB

# Belt-and-suspenders: ``get_stats`` calls ``score_open_trades`` first, which
# hits the Rust server only for OPEN trades. This module inserts ONLY
# already-resolved rows (win/loss/expired), so no open rows exist and no network
# call is triggered; overriding it with a no-op for the whole module guarantees
# the aggregation test is fully hermetic regardless. (A module-level override is
# used instead of the function-scoped ``monkeypatch`` fixture, which Hypothesis
# does not reset between generated inputs.)
journal.score_open_trades = lambda symbol=None: 0

_SYMBOL = "DEBATEAGGTEST"
_TIMEFRAME = "15m"

# The three categorical Debate_Consensus values recognised by the debate tag,
# plus a ``None`` sentinel that maps to a NON-debate decision (no debate entry ->
# ``db:unknown``). Recording across all of these exercises every db: bucket and
# their separate grouping.
_CONSENSUS_VALUES = ["strong_agree", "lean", "contested", None]

# Already-scored statuses only (no 'open' -> no candle fetch / network).
_STATUSES = ["win", "loss", "expired"]


def _make_decision(consensus) -> dict:
    """A BUY decision whose only varying dimension is the debate consensus.

    All other defensibility dimensions are absent (so their tags resolve to
    fixed unknown/neutral values), which means the resulting ``setup_key`` is
    fully determined by the ``db:`` tag — trades sharing a consensus aggregate
    into the same group and differing consensuses aggregate into separate groups.

    ``consensus is None`` models a non-DEBATE decision that carries no debate
    entry at all (so its tag collapses to ``db:unknown``).
    """
    decision = {
        "action": "BUY",
        "entry": 100.0,
        "stop_loss": 99.0,
        "take_profit": 103.0,
        "defensibility": {},
    }
    if consensus is not None:
        decision["defensibility"]["debate"] = {
            "bull_stance": "...",
            "bear_stance": "...",
            "consensus": consensus,
            "conviction": 6,
            "conviction_basis": "...",
        }
    return decision


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
# Layer 1 (pure): the debate dimension participates in grouping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 25: Aggregation groups by the debate-extended setup key
@settings(max_examples=100, deadline=None)
@given(
    consensus_a=st.sampled_from(_CONSENSUS_VALUES),
    consensus_b=st.sampled_from(_CONSENSUS_VALUES),
)
def test_property_25_setup_key_groups_by_debate_dimension(consensus_a, consensus_b):
    """Validates: Requirements 9.5

    The derived ``setup_key`` always carries exactly one ``db:`` dimension at a
    FIXED final position and is deterministic. Two decisions identical except for
    their debate consensus yield the SAME ``setup_key`` iff their consensus tags
    are equal, and DIFFERENT ``setup_key`` values when the consensus tags differ
    — so they aggregate into separate groups.
    """
    decision_a = _make_decision(consensus_a)
    decision_b = _make_decision(consensus_b)

    tags_a = journal.derive_setup_tags(decision_a)
    tags_b = journal.derive_setup_tags(decision_b)
    key_a = journal.setup_key_from_tags(tags_a)
    key_b = journal.setup_key_from_tags(tags_b)

    # Exactly one db: dimension at its fixed position; the options ``opt:`` and
    # opportunity ``tier:`` dimensions are appended after it (tier: is now final).
    db_tags_a = [t for t in tags_a if t.startswith("db:")]
    assert len(db_tags_a) == 1, f"expected exactly one db: tag, got {db_tags_a}"
    assert tags_a[-1].startswith("tier:"), "tier: tag must be at the fixed final position"

    # Determinism: re-deriving the same decision yields an identical key.
    assert journal.setup_key_from_tags(journal.derive_setup_tags(decision_a)) == key_a

    # The bare consensus value the journal collapses each decision to.
    tag_a = db_tags_a[0]
    tag_b = next(t for t in tags_b if t.startswith("db:"))

    if tag_a == tag_b:
        # Same db: bucket -> identical setup_key (they aggregate together).
        assert key_a == key_b, (
            f"identical db: tags must share a setup_key: {key_a!r} vs {key_b!r}"
        )
    else:
        # Different db: bucket -> different setup_key (separate groups).
        assert key_a != key_b, (
            f"different db: tags must yield different setup_keys: "
            f"{key_a!r} vs {key_b!r}"
        )


# Feature: multi-agent-debate, Property 25: Aggregation groups by the debate-extended setup key
@settings(max_examples=100, deadline=None)
@given(
    consensus_a=st.sampled_from(["strong_agree", "lean", "contested"]),
    consensus_b=st.sampled_from(["strong_agree", "lean", "contested"]),
)
def test_property_25_distinct_consensus_yields_distinct_keys(consensus_a, consensus_b):
    """Validates: Requirements 9.5

    Two decisions identical except for a RECOGNISED debate consensus produce
    DIFFERENT ``setup_key`` values exactly when the consensus values differ —
    directly proving the debate consensus is a grouping dimension.
    """
    key_a = journal.setup_key_from_tags(journal.derive_setup_tags(_make_decision(consensus_a)))
    key_b = journal.setup_key_from_tags(journal.derive_setup_tags(_make_decision(consensus_b)))

    if consensus_a == consensus_b:
        assert key_a == key_b
    else:
        assert key_a != key_b, (
            f"consensus {consensus_a!r} vs {consensus_b!r} must yield distinct "
            f"setup_keys, got {key_a!r} == {key_b!r}"
        )
        # And the only difference is the db: dimension (the opt: and opportunity
        # tier: dimensions follow it, identical for both decisions).
        parts_a = key_a.split("|")
        parts_b = key_b.split("|")
        assert f"db:{consensus_a}" in parts_a
        assert f"db:{consensus_b}" in parts_b
        # Every component EXCEPT the db: one is identical across the two keys.
        non_db_a = [p for p in parts_a if not p.startswith("db:")]
        non_db_b = [p for p in parts_b if not p.startswith("db:")]
        assert non_db_a == non_db_b


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 (get_stats): aggregation groups by the debate-extended setup_key
# ─────────────────────────────────────────────────────────────────────────────

@st.composite
def _trade(draw):
    """A single already-scored trade spec: (consensus, status, r_multiple)."""
    consensus = draw(st.sampled_from(_CONSENSUS_VALUES))
    status = draw(st.sampled_from(_STATUSES))
    r_multiple = draw(
        st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False)
    )
    return (consensus, status, r_multiple)


# Feature: multi-agent-debate, Property 25: Aggregation groups by the debate-extended setup key
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(trades=st.lists(_trade(), min_size=1, max_size=30))
def test_property_25_get_stats_aggregates_by_debate_setup_key(trades):
    """Validates: Requirements 9.5

    Recording a set of scored trades whose only varying dimension is the debate
    consensus and then aggregating via ``get_stats`` reports, per debate-extended
    ``setup_key``, a win-rate equal to wins/(wins+losses), an expectancy equal to
    the mean R-multiple of the scored trades, and a low-sample weak-prior flag
    matching the scored count against the configured threshold. Decisions
    differing only in consensus form SEPARATE ``by_setup`` groups.
    """
    # Independence between generated examples: clear any prior backtest rows.
    journal.purge(source="backtest")

    # Record each trade and accumulate the independently-expected grouping by the
    # debate-extended setup_key.
    expected: dict = {}
    for consensus, status, r_multiple in trades:
        decision = _make_decision(consensus)
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
    # no spurious groups beyond those recorded. Distinct consensuses -> distinct
    # keys -> separate groups.
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

        # Low-sample weak-prior flag (R9.5).
        assert group["low_sample"] == (scored < threshold), (
            f"low_sample for {key}: {group['low_sample']} != "
            f"expected {(scored < threshold)} (scored={scored}, threshold={threshold})"
        )
        # The group's scored count must match what we recorded.
        assert group["trades_scored"] == scored
