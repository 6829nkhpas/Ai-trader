"""Property-based test for degenerate-input robustness & weak-prior flags
(attribution.py, task 6.5).

Feature: feature-attribution-pruning

This module implements design **Property 13: Degenerate-input robustness and
weak-prior flags**:

    For any list of trade rows, ``build_attribution_report`` never raises; when
    there are zero Scored_Trades it returns an empty dimensions list flagged
    ``insufficient_data``; it flags ``weak_prior`` exactly when total scored is
    below ``global_min_scored``; and it flags a value's stats ``weak_prior``
    exactly when that value's count is below ``min_sample_value``.

Validates: Requirements 5.3, 4.4, 5.2.

``build_attribution_report`` is a TOTAL pure function over its in-memory
``rows``: it must never raise no matter how garbage the input — empty lists,
rows that are not dicts (ints, ``None``, strings), rows missing fields, rows
with malformed / empty ``setup_key`` fingerprints, and journals that carry no
resolved win/loss outcome at all. On such input the report still has to satisfy
the sufficiency contract (``insufficient_data`` iff zero Scored_Trades, and an
empty ``dimensions`` list in that case), the report-level ``weak_prior`` gate
(``total_scored < global_min_scored``), and the per-value ``weak_prior`` gate
(``count < min_sample_value``).

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_determinism_properties.py``; this generator additionally
mixes well-formed rows with heavily degenerate garbage so the entry point is
stressed across the whole input space.
"""

import math
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
    is_scored_trade,
)


# ── A deterministic config that exercises both weak_prior boundaries ──────────
# Values are chosen so that, over journals of up to ~40 rows, the report-level
# and per-value weak_prior flags flip on BOTH sides of their gates:
#   * global_min_scored = 20  -> total_scored lands below AND at/above the gate
#   * min_sample_value  = 4   -> per-value counts land below AND at/above the gate
# (min_sample_dimension / contribution_threshold / down_weight_factor are fixed
# to valid in-range values; they do not affect the flags under test.)
_CONFIG = AttributionConfig(
    min_sample_dimension=10,
    min_sample_value=4,
    contribution_threshold=0.15,
    global_min_scored=20,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)


# ── Building blocks shared with the well-formed generators ────────────────────
_DIMENSIONS = [
    "dir", "macro", "pred", "va", "regime",
    "rs", "fc", "tm", "sess", "db", "opt",
]
_VALUES = [
    "BUY", "SELL", "aligned", "below", "above",
    "trend-favorable", "leader-aligned", "strong", "weak", "morning",
    "unknown", "",
]

# A finite, usable R-multiple (a *scored* row must carry one of these).
_finite_r = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)
# A non-finite / unusable R-multiple: None, NaN, or +-inf.
_nonfinite_r = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)

_source = st.sampled_from(["backtest", "live", "LIVE", "Backtest", None, "", "paper"])


@st.composite
def _setup_key(draw):
    """A random ``setup_key``: a structured dimension:value fingerprint, OR one of
    a set of malformed / empty / degenerate keys the parser must tolerate."""
    kind = draw(st.integers(min_value=0, max_value=3))
    if kind == 0:
        # Malformed / empty / degenerate keys the parser must tolerate.
        return draw(st.sampled_from(
            ["", "   ", "|", "||", "a||b", ":", ":trend", "regime", "regime:",
             "regime:unknown", "fc:aligned:strong", "x:|y:unknown|z"]
        ))
    if kind == 1:
        # Wholly arbitrary text (may also be a non-string below, in garbage rows).
        return draw(st.text(max_size=40))
    # Structured: a random non-empty subset of dimensions, each with a random
    # value. dict() collapses duplicate dimensions deterministically.
    spec = draw(st.dictionaries(
        keys=st.sampled_from(_DIMENSIONS),
        values=st.sampled_from(_VALUES),
        min_size=1,
        max_size=6,
    ))
    return "|".join(f"{d}:{v}" for d, v in spec.items())


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
    """A guaranteed NON-scored DICT row (non-resolving status, or unusable R)."""
    setup_key = draw(_setup_key())
    source = draw(_source)
    if draw(st.booleans()):
        return {
            "setup_key": setup_key,
            "status": draw(st.sampled_from(
                ["open", "expired", "hold", "OPEN", "", "pending"])),
            "r_multiple": draw(st.one_of(_finite_r, _nonfinite_r)),
            "source": source,
        }
    return {
        "setup_key": setup_key,
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_nonfinite_r),
        "source": source,
    }


@st.composite
def _garbage_row(draw):
    """A heavily degenerate "row" that is NOT a usable Scored_Trade dict.

    Covers: non-dict scalars (int / float / None / str / list), dicts missing
    required fields, dicts with malformed / non-string ``setup_key`` and
    non-string ``status`` / ``r_multiple``. ``build_attribution_report`` must
    tolerate every one of these without raising.
    """
    kind = draw(st.integers(min_value=0, max_value=5))
    if kind == 0:
        # Not a dict at all.
        return draw(st.one_of(
            st.none(),
            st.integers(),
            st.floats(allow_nan=True, allow_infinity=True),
            st.text(max_size=20),
            st.lists(st.integers(), max_size=3),
            st.booleans(),
        ))
    if kind == 1:
        # Empty dict — missing every field.
        return {}
    if kind == 2:
        # Dict missing some fields (only a setup_key, no status / r_multiple).
        return {"setup_key": draw(_setup_key())}
    if kind == 3:
        # Dict with a non-string / malformed setup_key plus odd field types.
        return {
            "setup_key": draw(st.one_of(
                st.none(), st.integers(), st.lists(st.text(max_size=3), max_size=3),
                st.dictionaries(st.text(max_size=3), st.text(max_size=3), max_size=2),
            )),
            "status": draw(st.one_of(st.none(), st.integers(), st.text(max_size=8))),
            "r_multiple": draw(st.one_of(
                st.none(), st.text(max_size=5), st.just(float("nan")))),
        }
    if kind == 4:
        # A win/loss "row" whose r_multiple is a non-numeric STRING.
        return {
            "setup_key": draw(_setup_key()),
            "status": draw(st.sampled_from(["win", "loss"])),
            "r_multiple": draw(st.text(max_size=6)),
        }
    # Arbitrary dict of arbitrary keys -> arbitrary scalar values.
    return draw(st.dictionaries(
        keys=st.text(max_size=8),
        values=st.one_of(st.none(), st.integers(), st.text(max_size=8), _finite_r),
        max_size=5,
    ))


@st.composite
def _mixed_journal(draw):
    """A journal that MIXES well-formed rows (scored / non-scored) with garbage.

    Each element is independently chosen to be a guaranteed Scored_Trade, a
    guaranteed non-scored dict, or a heavily degenerate garbage value, so the
    report sees realistic per-value aggregation AND maximal robustness stress in
    the same list. ``min_size=0`` also covers the empty-journal case.
    """
    return draw(st.lists(
        st.one_of(_scored_row(), _non_scored_row(), _garbage_row()),
        min_size=0,
        max_size=40,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Property 13 (task 6.5): Degenerate-input robustness and weak-prior flags
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 13: For any list of trade rows, build_attribution_report never raises; when there are zero Scored_Trades it returns an empty dimensions list flagged insufficient_data; it flags weak_prior exactly when total scored is below global_min_scored; and it flags a value's stats weak_prior exactly when that value's count is below min_sample_value.
@settings(max_examples=100, deadline=None)
@given(rows=_mixed_journal())
def test_property_13_degenerate_robustness_and_weak_prior_flags(rows):
    """Feature: feature-attribution-pruning, Property 13: degenerate-input
    robustness and the report-level / per-value weak_prior flags.

    Validates: Requirements 5.3, 4.4, 5.2
    """
    # (1) build_attribution_report never raises for any generated input.
    report = build_attribution_report(rows, _CONFIG)

    # Independent ground-truth scored count: count rows ONCE via is_scored_trade
    # (the same total semantics the report documents), so the flags are checked
    # against a value computed without trusting the report's own tally.
    expected_total_scored = sum(1 for r in rows if is_scored_trade(r))
    assert report["total_scored"] == expected_total_scored

    # (2) insufficient_data is True iff total_scored == 0, and then dimensions == [].
    assert report["insufficient_data"] is (report["total_scored"] == 0)
    if report["insufficient_data"]:
        assert report["dimensions"] == []

    # (3) report-level weak_prior is True iff total_scored < global_min_scored.
    assert report["weak_prior"] is (
        report["total_scored"] < _CONFIG.global_min_scored
    )

    # (4) every per-value Dimension_Stats: weak_prior True iff count < min_sample_value.
    for dimension in report["dimensions"]:
        for stats in dimension["values"]:
            assert stats["weak_prior"] is (
                stats["count"] < _CONFIG.min_sample_value
            )
