"""Property-based test for per-bin conviction calibration correctness
(calibration.py ``conviction_calibration``, task 14.2).

Feature: multi-agent-debate

This module implements design **Property 26: Conviction-bin calibration is
correct per bin**:

    For ANY set of recorded journal rows, each non-empty conviction bin reports a
    ``win_rate`` equal to its wins divided by its total (wins + losses) and a
    ``mean_conviction`` equal to the mean of its members' convictions; every empty
    bin reports ``None`` for both statistics (never a number, never a divide by
    zero).

Validates: Requirements 10.1.

Implementation under test: the PURE helper ``calibration.conviction_calibration``
(no DB reads / candle fetches / backtest). This test reuses the module's own
membership predicates (``_is_scored_debate_row``, ``consensus_of``, ``_bin_index``)
to define the expected per-bin membership, then HAND-COMPUTES the expected
mean-conviction and win-rate for each bin and asserts the reported aggregates
agree, and that empty bins report ``None``.

The sys.path / import pattern mirrors the sibling ``test_calibration_*`` modules:
the service directory (one level up) is prepended to ``sys.path`` so
``calibration`` is importable when pytest is run from anywhere.
"""

import json
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (calibration.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from calibration import (  # noqa: E402
    DEFAULT_CONVICTION_BINS,
    _bin_index,
    _is_scored_debate_row,
    conviction_calibration,
)

_TOL = 1e-6  # the helper rounds reported statistics to 4 decimals


# A journal row generator. Each field independently spans the relevant space so
# the generated rows exercise every filter branch in ``_is_scored_debate_row``:
#   * status: scored (win/loss) and unscored (open/expired/hold) outcomes.
#   * conviction: ints inside [0, 100], on the bin edges, AND out of range
#     (negative / > 100) so the unplaced-conviction path is hit; occasionally a
#     non-numeric value so the numeric guard is exercised.
#   * mode: "DEBATE" (qualifies) and other modes (qualifies only via a db: tag).
#   * setup_tags: a JSON-encoded list string OR a python list, carrying a db:
#     consensus dimension (strong_agree/lean/contested/unknown) plus noise tags.
#   * r_multiple: a finite R value (unused by this property but present as in
#     real rows).
_STATUSES = ["win", "loss", "open", "expired", "hold"]
_MODES = ["DEBATE", "RANGE", "TREND", "", "scan"]
_DB_VALUES = ["strong_agree", "lean", "contested", "unknown"]


@st.composite
def _journal_row(draw):
    status = draw(st.sampled_from(_STATUSES))
    mode = draw(st.sampled_from(_MODES))

    # Conviction: mostly ints across and beyond [0, 100] (incl. edges), and
    # sometimes a non-numeric value to exercise the numeric guard.
    conviction = draw(
        st.one_of(
            st.integers(min_value=-50, max_value=150),
            st.sampled_from([0, 20, 40, 60, 80, 100]),
            st.none(),
            st.sampled_from(["", "high", None]),
        )
    )

    # setup_tags: a db: dimension tag (real class, unknown, or absent) plus
    # arbitrary noise, encoded as a JSON string list OR a raw python list.
    tags = []
    dir_tag = draw(st.sampled_from(["dir:BUY", "dir:SELL", None]))
    if dir_tag:
        tags.append(dir_tag)
    db_value = draw(st.sampled_from(_DB_VALUES + [None]))
    if db_value is not None:
        tags.append(f"db:{db_value}")
    if draw(st.booleans()):
        tags.append("regime:trending")

    encode_as_list = draw(st.booleans())
    setup_tags = tags if encode_as_list else json.dumps(tags)

    return {
        "status": status,
        "conviction": conviction,
        "mode": mode,
        "setup_tags": setup_tags,
        "r_multiple": draw(
            st.floats(min_value=-5.0, max_value=5.0,
                      allow_nan=False, allow_infinity=False)
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 26: Conviction-bin calibration is correct per bin
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 26: Conviction-bin calibration is correct per bin
@settings(max_examples=100, deadline=None)
@given(rows=st.lists(_journal_row(), min_size=0, max_size=60))
def test_property_26_per_bin_calibration_is_correct(rows):
    """Validates: Requirements 10.1

    For any list of journal rows, each bin's reported ``mean_conviction`` equals
    the mean of the convictions of the scored DEBATE rows placed in that bin, and
    its ``win_rate`` equals wins / (wins + losses) of those rows; empty bins
    report ``None`` for both.
    """
    report = conviction_calibration(rows)

    # ── Independently recompute the expected per-bin membership, reusing the
    # module's own filtering/placement predicates so membership is defined
    # identically, then HAND-COMPUTE the aggregates. ─────────────────────────
    expected_members = [[] for _ in DEFAULT_CONVICTION_BINS]
    for r in rows:
        if not (isinstance(r, dict) and _is_scored_debate_row(r)):
            continue
        idx = _bin_index(float(r.get("conviction")), DEFAULT_CONVICTION_BINS)
        if idx is not None:
            expected_members[idx].append(r)

    # There is exactly one report entry per bin, in the same order.
    assert len(report["bins"]) == len(DEFAULT_CONVICTION_BINS)

    for (lo, hi), members, entry in zip(
        DEFAULT_CONVICTION_BINS, expected_members, report["bins"]
    ):
        # Bin edges/label are reported as the configured partition.
        assert entry["lower"] == lo
        assert entry["upper"] == hi

        wins = sum(1 for r in members if str(r.get("status")).lower() == "win")
        losses = sum(1 for r in members if str(r.get("status")).lower() == "loss")
        convs = [float(r.get("conviction")) for r in members]

        assert entry["count"] == len(members)
        assert entry["wins"] == wins
        assert entry["losses"] == losses
        assert entry["applicable"] == (len(members) > 0)

        if not members:
            # Empty bin: both statistics are None (never a number, no divide
            # by zero).
            assert entry["mean_conviction"] is None
            assert entry["win_rate"] is None
            continue

        # Non-empty bin: mean conviction equals the mean of members' convictions.
        expected_mean = round(sum(convs) / len(convs), 4)
        assert entry["mean_conviction"] is not None
        assert math.isclose(entry["mean_conviction"], expected_mean, abs_tol=_TOL), (
            f"bin [{lo},{hi}] mean_conviction {entry['mean_conviction']} "
            f"!= expected {expected_mean}"
        )

        # Win-rate equals wins / (wins + losses) of the bin's members. Since
        # every scored row has status win or loss, wins + losses == count > 0.
        total = wins + losses
        assert total == len(members)
        expected_rate = round(wins / total, 4)
        assert entry["win_rate"] is not None
        assert math.isclose(entry["win_rate"], expected_rate, abs_tol=_TOL), (
            f"bin [{lo},{hi}] win_rate {entry['win_rate']} != expected {expected_rate}"
        )
