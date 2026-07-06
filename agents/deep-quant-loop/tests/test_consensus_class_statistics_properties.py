"""Property-based test for per-consensus-class statistics (calibration.py, task 14.4).

Feature: multi-agent-debate

This module implements design **Property 28: Per-consensus-class statistics are
correct**:

    For ANY set of journal rows, each consensus class's reported win-rate equals
    ``wins / (wins + losses)`` of the scored DEBATE rows in that class, and its
    expectancy equals the mean ``r_multiple`` of those rows (over the win/loss
    rows with a finite r); an empty class is reported not-applicable with
    ``win_rate`` and ``expectancy_r`` of ``None``.

Validates: Requirements 10.3.

The strategy generates lists of journal row dicts whose ``db:`` setup tag spans
``strong_agree``/``lean``/``contested``/``unknown`` (and occasionally a missing
tag), with varied ``status`` (win/loss/open/expired/hold), ``conviction`` values
(numeric and non-numeric), and ``r_multiple`` values (finite floats plus some
non-finite / ``None`` values to exercise the expectancy finite-filtering). For
each generated list it independently re-derives the expected per-class wins,
losses, win-rate and expectancy by reusing the production ``consensus_of`` and
``_is_scored_debate_row`` helpers, then asserts the ``by_consensus`` block of
``conviction_calibration`` matches exactly. The sys.path / import pattern mirrors
the sibling ``test_*`` modules.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (calibration.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import calibration  # noqa: E402
from calibration import (  # noqa: E402
    CONSENSUS_CLASSES,
    conviction_calibration,
    consensus_of,
)


# The db:<value> tag tokens we emit, spanning the three real classes plus the
# non-class ``unknown`` token and a sentinel for "no db: tag at all".
_DB_TAG_VALUES = ["strong_agree", "lean", "contested", "unknown", "__none__"]
_STATUSES = ["win", "loss", "open", "expired", "hold"]


def _is_num(v) -> bool:
    """Mirror of calibration._is_num for the independent expected computation."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@st.composite
def _journal_row(draw):
    """An arbitrary journal row dict exercising the consensus statistics path."""
    db_value = draw(st.sampled_from(_DB_TAG_VALUES))
    # Some unrelated noise tags interleaved with the (optional) db: tag.
    tags = draw(
        st.lists(
            st.sampled_from(["trend:up", "vol:high", "rsi:ob", "session:open"]),
            max_size=3,
        )
    )
    if db_value != "__none__":
        # Insert the db: tag at an arbitrary position to avoid positional bias.
        pos = draw(st.integers(min_value=0, max_value=len(tags)))
        tags.insert(pos, f"db:{db_value}")

    # conviction: usually numeric (a scored row needs it), occasionally bad.
    conviction = draw(
        st.one_of(
            st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
            st.none(),
            st.just("bad"),
        )
    )

    # r_multiple: finite floats plus non-finite / None to exercise filtering.
    r_multiple = draw(
        st.one_of(
            st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
            st.just(float("nan")),
            st.just(float("inf")),
            st.just(float("-inf")),
            st.none(),
            st.just("oops"),
        )
    )

    # mode: usually DEBATE, sometimes other so qualification leans on the db: tag.
    mode = draw(st.sampled_from(["DEBATE", "SINGLE", "", None]))

    return {
        "status": draw(st.sampled_from(_STATUSES)),
        "conviction": conviction,
        "mode": mode,
        "setup_tags": tags,
        "r_multiple": r_multiple,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 28: Per-consensus-class statistics are correct
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 28: Per-consensus-class statistics are correct
@settings(max_examples=100, deadline=None)
@given(rows=st.lists(_journal_row(), max_size=40))
def test_property_28_per_consensus_class_statistics_are_correct(rows):
    """Validates: Requirements 10.3

    For any set of journal rows, each consensus class's reported win-rate equals
    wins/(wins+losses) of the scored DEBATE rows in that class, and its
    expectancy equals the mean r_multiple of those rows (finite r only). An empty
    class is reported not-applicable with win_rate/expectancy_r of None.
    """
    result = conviction_calibration(rows)
    by_consensus = result["by_consensus"]

    # Independently group the scored DEBATE rows by their consensus class, using
    # the production helpers so the expected values track the implementation's
    # qualification rules exactly.
    scored = [r for r in rows if calibration._is_scored_debate_row(r)]

    for cls in CONSENSUS_CLASSES:
        members = [r for r in scored if consensus_of(r) == cls]
        wins = sum(1 for r in members if str(r.get("status")).lower() == "win")
        losses = sum(1 for r in members if str(r.get("status")).lower() == "loss")
        r_values = [
            float(r.get("r_multiple"))
            for r in members
            if str(r.get("status")).lower() in ("win", "loss") and _is_num(r.get("r_multiple"))
        ]

        total = wins + losses
        expected_win_rate = round(wins / total, 4) if total else None
        expected_expectancy = round(sum(r_values) / len(r_values), 4) if r_values else None

        report = by_consensus[cls]

        assert report["count"] == len(members), (
            f"{cls}: count {report['count']} != expected {len(members)}"
        )
        assert report["wins"] == wins, f"{cls}: wins {report['wins']} != expected {wins}"
        assert report["losses"] == losses, (
            f"{cls}: losses {report['losses']} != expected {losses}"
        )
        assert report["win_rate"] == expected_win_rate, (
            f"{cls}: win_rate {report['win_rate']} != expected {expected_win_rate}"
        )
        assert report["expectancy_r"] == expected_expectancy, (
            f"{cls}: expectancy_r {report['expectancy_r']} != expected {expected_expectancy}"
        )
        assert report["applicable"] is (len(members) > 0), (
            f"{cls}: applicable {report['applicable']} != expected {len(members) > 0}"
        )

        # An empty class is not-applicable with both stats None (R10.4 guard).
        if not members:
            assert report["applicable"] is False
            assert report["win_rate"] is None
            assert report["expectancy_r"] is None
