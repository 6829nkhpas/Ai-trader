"""Property-based test for empty-bin / empty-class not-applicable handling
(calibration.py ``conviction_calibration``, task 14.5).

Feature: multi-agent-debate

This module implements design **Property 29: Empty bins and classes are
not-applicable, never divide-by-zero**:

    For ANY set of journal rows (including empty lists, all-unscored rows,
    all-unknown-consensus rows, and malformed rows), ``conviction_calibration``
    never raises, every empty conviction bin and every empty Debate_Consensus
    class is reported as not-applicable (``applicable`` False with
    ``win_rate`` / ``mean_conviction`` / ``expectancy_r`` of ``None``), and no
    division-by-zero ever occurs.

Validates: Requirements 10.4.

Implementation under test: the pure measurement ``calibration.conviction_calibration``
(no DB reads, no candle fetches, no backtest). This test feeds it deliberately
adversarial row lists — empty lists, rows that are open/expired/hold only, rows
with no recorded conviction, rows tagged ``db:unknown`` only, malformed dicts
(missing keys, wrong types, ``None`` values), and non-dict entries mixed in — and
asserts the not-applicable contract holds for every empty bin and class while the
call always completes (never raises) and ``result["applicable"]`` exactly tracks
whether any scored trade was found.

The sys.path / import pattern mirrors the sibling calibration property tests in
this directory: the service directory (one level up) is prepended to ``sys.path``
so ``calibration`` is importable when pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (calibration.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from calibration import (  # noqa: E402
    CONSENSUS_CLASSES,
    conviction_calibration,
)

# ─────────────────────────────────────────────────────────────────────────────
# Adversarial row generators
#
# The goal is to stress the not-applicable / divide-by-zero paths, so the
# generators heavily favor rows that produce empty bins and empty consensus
# classes: unscored statuses, missing/non-numeric conviction, db:unknown tags,
# malformed shapes, and non-dict entries. A small fraction of well-formed scored
# DEBATE rows is also mixed in so the ``applicable`` flag is exercised both ways.
# ─────────────────────────────────────────────────────────────────────────────

# Statuses that NEVER qualify as scored (open/expired/hold/blank/garbage).
_UNSCORED_STATUS = st.sampled_from(
    ["open", "expired", "hold", "OPEN", "Hold", "", "pending", "cancelled", "???"]
)

# A junk value that may appear anywhere a number/string is expected.
_JUNK = st.one_of(
    st.none(),
    st.text(max_size=8),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)


@st.composite
def _unscored_row(draw):
    """A dict that is structurally row-like but never a scored DEBATE row."""
    row = {
        "status": draw(_UNSCORED_STATUS),
        "mode": draw(st.sampled_from(["DEBATE", "FIND", "MANAGE", "", None])),
        "conviction": draw(st.one_of(st.none(), st.floats(0, 100), _JUNK)),
        "setup_key": draw(st.sampled_from(["db:unknown", "", "trend|db:unknown", None])),
    }
    return row


@st.composite
def _malformed_row(draw):
    """A dict missing keys / carrying wrong types / None values."""
    candidates = {
        "status": draw(_JUNK),
        "mode": draw(_JUNK),
        "conviction": draw(_JUNK),
        "r_multiple": draw(_JUNK),
        "setup_key": draw(_JUNK),
        "setup_tags": draw(_JUNK),
    }
    # Keep an arbitrary subset of the keys so rows can be missing fields entirely.
    keys = draw(st.lists(st.sampled_from(list(candidates)), max_size=6, unique=True))
    return {k: candidates[k] for k in keys}


@st.composite
def _scored_debate_row(draw):
    """A well-formed scored DEBATE row that DOES qualify for the measurement.

    Mixed in so the ``applicable`` flag and non-empty bins/classes are exercised
    alongside the adversarial rows — the not-applicable contract must still hold
    for whichever bins / classes remain empty.
    """
    cls = draw(st.sampled_from(CONSENSUS_CLASSES))
    return {
        "status": draw(st.sampled_from(["win", "loss"])),
        "mode": "DEBATE",
        "conviction": draw(st.floats(min_value=0.0, max_value=100.0)),
        "r_multiple": draw(st.floats(min_value=-5.0, max_value=5.0)),
        "setup_key": f"trend|db:{cls}",
    }


# Non-dict junk entries that may be interleaved into the row list.
_NON_DICT_ENTRY = st.one_of(
    st.none(),
    st.integers(),
    st.text(max_size=8),
    st.floats(allow_nan=True, allow_infinity=True),
    st.lists(st.integers(), max_size=3),
    st.tuples(st.integers(), st.integers()),
)

# A heterogeneous row list: heavily adversarial, with occasional valid rows.
_rows = st.lists(
    st.one_of(
        _unscored_row(),
        _malformed_row(),
        _NON_DICT_ENTRY,
        _scored_debate_row(),
    ),
    min_size=0,
    max_size=40,
)

# A custom (non-default) bin definition to exercise the ``bins`` argument path.
_CUSTOM_BINS = [(0, 50), (50, 100)]


def _count_scored(rows):
    """Recompute the expected scored-trade count independently of the impl.

    Mirrors ``_is_scored_debate_row``: a dict with status win/loss, a finite
    numeric conviction (bool excluded), and either mode == DEBATE or a real
    db: consensus tag in setup_key / setup_tags.
    """
    import json as _json
    import math as _math

    def _is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and _math.isfinite(v)

    def _tags(row):
        # Faithful mirror of calibration._tags_of (list -> JSON-string -> setup_key).
        raw = row.get("setup_tags")
        if isinstance(raw, list):
            return [str(t) for t in raw]
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, list):
                    return [str(t) for t in parsed]
            except Exception:
                pass
        key = row.get("setup_key")
        if isinstance(key, str) and key:
            return key.split("|")
        return []

    def _consensus(row):
        for tag in _tags(row):
            t = str(tag).strip()
            if t.startswith("db:"):
                val = t[len("db:"):].strip().lower()
                return val if val in CONSENSUS_CLASSES else None
        return None

    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "").strip().lower() not in ("win", "loss"):
            continue
        if not _is_num(r.get("conviction")):
            continue
        mode = str(r.get("mode") or "").strip().upper()
        if mode == "DEBATE" or _consensus(r) is not None:
            n += 1
    return n


def _assert_not_applicable_contract(result):
    """Assert every empty bin / empty consensus class is reported not-applicable."""
    # Every empty bin: applicable False, win_rate None, mean_conviction None.
    for b in result["bins"]:
        if b["count"] == 0:
            assert b["applicable"] is False, f"empty bin marked applicable: {b!r}"
            assert b["win_rate"] is None, f"empty bin has win_rate: {b!r}"
            assert b["mean_conviction"] is None, f"empty bin has mean_conviction: {b!r}"

    # Every empty consensus class: applicable False, win_rate None, expectancy_r None.
    for cls, c in result["by_consensus"].items():
        if c["count"] == 0:
            assert c["applicable"] is False, f"empty class {cls} marked applicable: {c!r}"
            assert c["win_rate"] is None, f"empty class {cls} has win_rate: {c!r}"
            assert c["expectancy_r"] is None, f"empty class {cls} has expectancy_r: {c!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 29: Empty bins and classes are not-applicable, never divide-by-zero
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 29: Empty bins and classes are not-applicable, never divide-by-zero
@settings(max_examples=100, deadline=None)
@given(rows=_rows, use_custom_bins=st.booleans())
def test_property_29_empty_bins_and_classes_not_applicable(rows, use_custom_bins):
    """Validates: Requirements 10.4

    For any adversarial row list, ``conviction_calibration`` never raises, every
    empty bin and empty consensus class is reported not-applicable (applicable
    False, win_rate / mean_conviction / expectancy_r None), no divide-by-zero
    occurs, and ``result["applicable"]`` equals (trades_scored > 0). Exercised
    with both the default binning (``bins=None``) and a custom ``bins`` arg.
    """
    bins_arg = _CUSTOM_BINS if use_custom_bins else None

    # ── Never raises: the call completes and returns a dict (R10.4). ──────────
    result = conviction_calibration(rows, bins=bins_arg)
    assert isinstance(result, dict)
    assert "bins" in result and "by_consensus" in result

    # ── Every empty bin / class is reported not-applicable (no /0). ───────────
    _assert_not_applicable_contract(result)

    # ── trades_scored and the top-level applicable flag agree: applicable is
    #    True exactly when at least one scored trade was found (R10.4). ────────
    assert result["applicable"] == (result["trades_scored"] > 0)

    # Independent recomputation of the scored-row count confirms trades_scored.
    assert result["trades_scored"] == _count_scored(rows)

    # All three consensus classes are always present in the report.
    assert set(result["by_consensus"].keys()) == set(CONSENSUS_CLASSES)
