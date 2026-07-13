"""Property-based test for ranking by contribution (attribution.py, task 5.5).

Feature: feature-attribution-pruning

This module implements design **Property 11: Ranking by contribution**:

    For any list of trade rows, the report's dimensions are ordered by
    contribution descending (with not-meaningful contributions ordered last),
    and the rank field is the 1-based position in that order.

Validates: Requirements 3.1.

``build_attribution_report`` wires the pure pipeline end to end and returns a
ranked ``report["dimensions"]`` list of Dimension_Report entries. Each entry
carries a ``"contribution"`` (a float, or ``None`` when not meaningful) plus its
``"contribution_meaningful"`` flag, and a 1-based ``"rank"``.

``rank_and_recommend`` (read directly) sorts the entries by the exact key tuple::

    (0 if contribution is not None else 1,          # meaningful first, None last
     -contribution if contribution is not None else 0.0,   # contribution DESC
     str(dimension))                                 # tiebreak: name ascending

and then assigns ``rank`` as the 1-based position in that order. This property
verifies that full ordering — meaningful-first, then contribution descending,
then dimension name ascending — and that the ranks are exactly ``1..N`` in list
order, across the journal space.

To produce a MIX of meaningful and not-meaningful contributions, this test uses
a deterministic config with a LOW ``min_sample_dimension`` (3): dimensions that
accumulate enough scored trades clear the gate and earn a meaningful (float)
contribution, while rarely-seen / malformed-key dimensions stay below it and are
reported not-meaningful (``None``) — so both the descending-contribution branch
and the None-last branch of the sort key are exercised.

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_recommendation_threshold_properties.py`` /
``tests/test_attribution_statistical_honesty_properties.py`` (kept local to this
file for consistency).
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from attribution import (  # noqa: E402
    AttributionConfig,
    build_attribution_report,
)

# A deterministic, fixed configuration. ``min_sample_dimension`` is set LOW (3)
# relative to the journals generated below, so well-represented dimensions CLEAR
# the sample gate and earn a meaningful (float) contribution, while rarely-seen
# or malformed-key dimensions stay below it and are reported not-meaningful
# (None) — guaranteeing a MIX that exercises both branches of the sort key.
# ``min_sample_value`` is 1 so per-value stats stay usable.
_CONFIG = AttributionConfig(
    min_sample_dimension=3,
    min_sample_value=1,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)


# ── Shared journal generators (local to this file) ────────────────────────────
# A SMALL pool of common dimensions/values so generated keys collide heavily
# across rows (driving per-dimension scored counts above the sample gate ->
# meaningful contributions), plus a few RARE dimensions that seldom recur (so
# they stay below the gate -> not-meaningful contributions). The combination
# yields a mix of meaningful and None contributions on most examples.
_COMMON_DIMENSIONS = ["dir", "regime", "rs", "fc", "opt"]
_RARE_DIMENSIONS = ["sess", "macro", "pred", "va", "tm", "db"]
_VALUES = ["BUY", "SELL", "aligned", "below", "strong", "weak"]

# A finite, usable R-multiple (a *scored* row must carry one of these). Spread on
# a small grid so per-value mean R-multiples both sometimes coincide and
# sometimes diverge — producing a range of contribution magnitudes to rank.
_finite_r = st.sampled_from([-3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0])

# A non-finite / unusable R-multiple: None, NaN, or ±inf. A win/loss row carrying
# one of these is NOT a Scored_Trade.
_nonfinite_r = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)


@st.composite
def _setup_key(draw):
    """A random ``setup_key``: a structured dimension:value fingerprint, or one of
    a set of malformed / empty keys (robustness coverage)."""
    kind = draw(st.integers(min_value=0, max_value=5))
    if kind == 0:
        # Malformed / empty / degenerate keys the parser must tolerate.
        return draw(st.sampled_from(
            ["", "   ", "|", "||", "a||b", ":", ":trend", "regime", "regime:",
             "regime:unknown", "fc:aligned:strong", "x:|y:unknown|z"]
        ))
    # Structured: a random non-empty subset of dimensions, each with a random
    # value. Common dimensions dominate (so they clear the gate); a rare
    # dimension is occasionally mixed in (so it stays below the gate).
    spec = draw(st.dictionaries(
        keys=st.sampled_from(_COMMON_DIMENSIONS),
        values=st.sampled_from(_VALUES),
        min_size=1,
        max_size=len(_COMMON_DIMENSIONS),
    ))
    if draw(st.integers(min_value=0, max_value=3)) == 0:
        # Occasionally tack on a rare dimension that seldom recurs.
        rare = draw(st.sampled_from(_RARE_DIMENSIONS))
        spec[rare] = draw(st.sampled_from(_VALUES))
    return "|".join(f"{d}:{v}" for d, v in spec.items())


_source = st.sampled_from(["backtest", "live", "LIVE", "Backtest", None, "", "paper"])


@st.composite
def _scored_row(draw):
    """A guaranteed Scored_Trade: win/loss status with a finite ``r_multiple``."""
    return {
        "setup_key": draw(_setup_key()),
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_finite_r),
        "source": draw(_source),
        "symbol": draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None])),
    }


@st.composite
def _non_scored_row(draw):
    """A guaranteed NON-scored row (non-resolving status, or unusable r_multiple)."""
    setup_key = draw(_setup_key())
    source = draw(_source)
    symbol = draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None]))
    if draw(st.booleans()):
        return {
            "setup_key": setup_key,
            "status": draw(st.sampled_from(["open", "expired", "hold", "OPEN", "", "pending"])),
            "r_multiple": draw(st.one_of(_finite_r, _nonfinite_r)),
            "source": source,
            "symbol": symbol,
        }
    return {
        "setup_key": setup_key,
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_nonfinite_r),
        "source": source,
        "symbol": symbol,
    }


@st.composite
def _journal_row(draw):
    """An arbitrary trade row: mostly scored (so the gate is cleared), some not."""
    if draw(st.integers(min_value=0, max_value=3)) != 0:
        return draw(_scored_row())
    return draw(_non_scored_row())


@st.composite
def _journal(draw, min_size=0, max_size=60):
    """A random, deliberately LARGER journal: a list of arbitrary trade rows.

    Larger (relative to ``min_sample_dimension == 3``) and drawn over a small
    common-dimension pool (with occasional rare dimensions) so per-dimension
    scored counts straddle the sample gate — producing both meaningful and
    not-meaningful contributions to rank.
    """
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


def _sort_key(entry):
    """The EXACT ranking sort key tuple used by ``rank_and_recommend``:
    meaningful-first (None last), then contribution descending, then dimension
    name ascending."""
    contribution = entry["contribution"]
    return (
        0 if contribution is not None else 1,
        -contribution if contribution is not None else 0.0,
        str(entry["dimension"]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 11 (task 5.5): Ranking by contribution
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 11: For any list of trade rows, the report's dimensions are ordered by contribution descending (with not-meaningful contributions ordered last), and the rank field is the 1-based position in that order.
@settings(max_examples=100, deadline=None)
@given(rows=_journal())
def test_property_11_ranking_by_contribution(rows):
    """Feature: feature-attribution-pruning, Property 11: the report's dimensions
    are ordered by the full ranking sort key (meaningful-first, then contribution
    descending, then dimension name ascending), and each entry's ``rank`` is its
    1-based position in that already-ordered list.

    Validates: Requirements 3.1
    """
    report = build_attribution_report(rows, _CONFIG)

    dimensions = report["dimensions"]
    assert isinstance(dimensions, list)

    # 1. The rank fields are exactly 1..N in list order (the list is already
    #    ordered; entry[i]["rank"] == i + 1).
    for i, entry in enumerate(dimensions):
        assert entry["rank"] == i + 1, (
            f"entry at index {i} (dimension {entry.get('dimension')!r}) has "
            f"rank={entry['rank']!r}; expected {i + 1}"
        )

    # 2. The ordering matches the full sort key exactly: meaningful-first, then
    #    contribution descending, then dimension name ascending. Verifying that
    #    the observed key sequence equals its own sort proves the list is ordered
    #    by precisely that key (no looser / different ordering passes).
    keys = [_sort_key(entry) for entry in dimensions]
    assert keys == sorted(keys), (
        "report dimensions are not ordered by (meaningful-first, contribution "
        f"desc, dimension name asc); observed key order: {keys}"
    )

    # Spell out the consecutive-pair guarantees the sort key encodes, so a
    # regression points at the specific ordering rule that broke.
    for prev, curr in zip(dimensions, dimensions[1:]):
        prev_meaningful = prev["contribution"] is not None
        curr_meaningful = curr["contribution"] is not None

        # A meaningful entry never follows a not-meaningful one.
        if not prev_meaningful:
            assert not curr_meaningful, (
                f"not-meaningful dimension {prev.get('dimension')!r} is followed "
                f"by meaningful dimension {curr.get('dimension')!r}"
            )

        if prev_meaningful and curr_meaningful:
            # Among meaningful entries: contribution is non-increasing, ties
            # broken by ascending dimension name.
            assert prev["contribution"] >= curr["contribution"], (
                f"contribution not non-increasing: {prev.get('dimension')!r}="
                f"{prev['contribution']} then {curr.get('dimension')!r}="
                f"{curr['contribution']}"
            )
            if prev["contribution"] == curr["contribution"]:
                assert str(prev["dimension"]) <= str(curr["dimension"]), (
                    "equal-contribution tie not broken by ascending dimension "
                    f"name: {prev.get('dimension')!r} before {curr.get('dimension')!r}"
                )

    # 3. All not-meaningful (contribution None) entries come after all meaningful
    #    ones: the index of the first not-meaningful entry (if any) is greater
    #    than the index of every meaningful entry.
    meaningful_indices = [
        i for i, e in enumerate(dimensions) if e["contribution"] is not None
    ]
    not_meaningful_indices = [
        i for i, e in enumerate(dimensions) if e["contribution"] is None
    ]
    # Also assert the flag agrees with the contribution value.
    for e in dimensions:
        assert e["contribution_meaningful"] is (e["contribution"] is not None)
    if meaningful_indices and not_meaningful_indices:
        assert max(meaningful_indices) < min(not_meaningful_indices), (
            "a not-meaningful dimension appears before a meaningful one"
        )
