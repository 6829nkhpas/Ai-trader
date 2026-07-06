"""Property-based test for the conviction-calibration reliability monotonic check.

Feature: multi-agent-debate

This module implements design **Property 27: Reliability flag matches the
monotonic check** for ``calibration.conviction_calibration`` (task 14.3):

    For ANY set of journal rows, ``reliability.non_decreasing`` is True iff the
    sequence of win-rates of the non-empty conviction bins (in increasing bin
    order) is non-decreasing.

Validates: Requirements 10.2.

The strategy generates arbitrary lists of journal row dicts. Most rows are
well-formed scored DEBATE-mode trades (``status`` win/loss, integer
``conviction`` in ``[0, 100]``, ``mode == "DEBATE"``, a ``db:<consensus>`` setup
tag, and a finite ``r_multiple``), with conviction values deliberately drawn so
that they cluster across bins and across statuses. This exercises BOTH monotonic
and non-monotonic win-rate sequences (e.g. crafted rows where a higher bin has a
lower win-rate). A sprinkling of noise rows (open/hold rows, non-debate rows,
malformed entries) verifies the filtering path without affecting the independent
oracle, which reads the per-bin win-rates straight out of the result.

For each generated list it calls ``conviction_calibration(rows)`` and, from the
returned ``bins`` (already ordered by increasing conviction), extracts the
win-rates of the non-empty bins, then independently checks that this sequence is
non-decreasing and asserts the result equals ``reliability.non_decreasing``. It
also asserts ``reliability.applicable == (#non-empty bins >= 2)`` and
``reliability.bins_compared == #non-empty bins``. The sys.path / import pattern
mirrors the sibling ``test_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (calibration.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from calibration import conviction_calibration  # noqa: E402

_CONSENSUS = ["strong_agree", "lean", "contested"]


@st.composite
def _scored_debate_row(draw):
    """A well-formed scored DEBATE-mode row usable for calibration.

    Conviction is drawn across the full [0, 100] scale so members land in every
    bin; status is independent of conviction so higher bins can end up with a
    LOWER win-rate, exercising the non-monotonic case.
    """
    return {
        "status": draw(st.sampled_from(["win", "loss"])),
        "conviction": draw(st.integers(min_value=0, max_value=100)),
        "mode": "DEBATE",
        "setup_tags": ["db:" + draw(st.sampled_from(_CONSENSUS))],
        "r_multiple": draw(
            st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False)
        ),
    }


@st.composite
def _noise_row(draw):
    """A row that should be excluded from the measurement (filtering noise)."""
    kind = draw(st.integers(min_value=0, max_value=3))
    if kind == 0:
        # Unscored outcome (open/hold/expired) -> excluded.
        return {
            "status": draw(st.sampled_from(["open", "hold", "expired", ""])),
            "conviction": draw(st.integers(min_value=0, max_value=100)),
            "mode": "DEBATE",
            "setup_tags": ["db:lean"],
        }
    if kind == 1:
        # Non-debate row with no db: tag -> excluded.
        return {
            "status": draw(st.sampled_from(["win", "loss"])),
            "conviction": draw(st.integers(min_value=0, max_value=100)),
            "mode": "STANDARD",
            "setup_tags": ["trend:up"],
        }
    if kind == 2:
        # Missing/non-numeric conviction -> excluded.
        return {
            "status": "win",
            "conviction": None,
            "mode": "DEBATE",
            "setup_tags": ["db:strong_agree"],
        }
    # Malformed / not a dict-shaped row.
    return draw(st.sampled_from([{}, {"foo": "bar"}]))


@st.composite
def _rows(draw):
    """An arbitrary list of journal rows: scored debate rows plus noise."""
    scored = draw(st.lists(_scored_debate_row(), min_size=0, max_size=30))
    noise = draw(st.lists(_noise_row(), min_size=0, max_size=8))
    rows = scored + noise
    draw(st.randoms()).shuffle(rows)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Property 27: Reliability flag matches the monotonic check
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 27: Reliability flag matches the monotonic check
@settings(max_examples=100, deadline=None)
@given(rows=_rows())
def test_property_27_reliability_matches_monotonic_check(rows):
    """Validates: Requirements 10.2

    ``reliability.non_decreasing`` is True iff the win-rates of the non-empty
    conviction bins (in increasing bin order) form a non-decreasing sequence;
    ``applicable`` holds iff there are >= 2 non-empty bins; ``bins_compared``
    equals the number of non-empty bins.
    """
    result = conviction_calibration(rows)

    reliability = result["reliability"]
    bin_reports = result["bins"]

    # Independent oracle: win-rates of the non-empty bins, in increasing bin
    # order (the result's bins are already ordered by increasing conviction).
    non_empty_rates = [
        b["win_rate"]
        for b in bin_reports
        if b["applicable"] and b["win_rate"] is not None
    ]

    expected_non_decreasing = all(
        non_empty_rates[i] <= non_empty_rates[i + 1]
        for i in range(len(non_empty_rates) - 1)
    )

    assert reliability["non_decreasing"] == expected_non_decreasing, (
        f"non_decreasing mismatch: flag={reliability['non_decreasing']} "
        f"expected={expected_non_decreasing} rates={non_empty_rates}"
    )
    assert reliability["applicable"] == (len(non_empty_rates) >= 2), (
        f"applicable mismatch: flag={reliability['applicable']} "
        f"non_empty_bins={len(non_empty_rates)}"
    )
    assert reliability["bins_compared"] == len(non_empty_rates), (
        f"bins_compared mismatch: flag={reliability['bins_compared']} "
        f"non_empty_bins={len(non_empty_rates)}"
    )
