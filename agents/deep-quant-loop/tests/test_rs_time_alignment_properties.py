"""Property-based test for time-alignment over common timestamps (rs.py, task 2.4).

Feature: relative-strength-context

This module implements design **Property 9: Time-alignment makes the result
depend only on common-timestamp candles**:

    ``time_align(symbol_candles, benchmark_candles)`` projects both sequences to
    two equal-length lists of valid rows whose timestamps are exactly the
    timestamps common to BOTH sequences, in ascending timestamp order. Because
    only the intersection survives, adding or removing candles at timestamps that
    are NOT common to both sequences does not change the aligned result.

Validates: Requirements 3.7.

The sys.path / import pattern mirrors the sibling ``test_rs_*_properties.py``
and ``test_regime_*_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
from rs import time_align  # noqa: E402


# ── Candle generation ─────────────────────────────────────────────────────────
# A candle is a dict-like OHLCV record carrying ``timestamp_ms`` plus
# open/high/low/close/volume. We only ever build *valid* candles (finite
# numbers) so that whether a row survives alignment is governed solely by whether
# its timestamp is common to both sequences — isolating Property 9 (R3.7) from
# the non-finite-exclusion concern (R3.2, covered by Property 8).

_price = st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)


@st.composite
def alignment_inputs(draw):
    """Generate symbol/benchmark candle sequences with overlapping and
    non-overlapping timestamps.

    Produces, for a set of distinct integer timestamps partitioned into three
    disjoint roles (common-to-both / symbol-only / benchmark-only):

      * ``sym_common`` / ``bench_common`` — the candle each sequence carries at a
        common timestamp (independent prices, so we can also confirm symbol rows
        are sourced from symbol candles and benchmark rows from benchmark
        candles),
      * ``sym_only`` / ``bench_only`` — candles at non-common timestamps,
      * shuffled full sequences and common-only sequences built from them.
    """
    # Distinct timestamps so alignment is unambiguous (no same-timestamp dups).
    all_ts = draw(
        st.lists(st.integers(min_value=0, max_value=100_000), min_size=0, max_size=24, unique=True)
    )

    common_ts, sym_only_ts, bench_only_ts = [], [], []
    for ts in all_ts:
        role = draw(st.sampled_from(("common", "sym", "bench")))
        (common_ts if role == "common" else sym_only_ts if role == "sym" else bench_only_ts).append(ts)

    def candle(ts):
        return {
            "timestamp_ms": ts,
            "open": draw(_price),
            "high": draw(_price),
            "low": draw(_price),
            "close": draw(_price),
            "volume": draw(_price),
        }

    sym_common = {ts: candle(ts) for ts in common_ts}
    bench_common = {ts: candle(ts) for ts in common_ts}
    sym_only = {ts: candle(ts) for ts in sym_only_ts}
    bench_only = {ts: candle(ts) for ts in bench_only_ts}

    # Common-only sequences (the baseline) and full sequences (with extra
    # non-common candles mixed in), each in an arbitrary order.
    sym_common_seq = draw(st.permutations(list(sym_common.values())))
    bench_common_seq = draw(st.permutations(list(bench_common.values())))
    sym_full_seq = draw(st.permutations(list(sym_common.values()) + list(sym_only.values())))
    bench_full_seq = draw(st.permutations(list(bench_common.values()) + list(bench_only.values())))

    return {
        "common_ts": common_ts,
        "sym_common": sym_common,
        "bench_common": bench_common,
        "sym_common_seq": list(sym_common_seq),
        "bench_common_seq": list(bench_common_seq),
        "sym_full_seq": list(sym_full_seq),
        "bench_full_seq": list(bench_full_seq),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 2.4): Time-alignment makes the result depend only on
# common-timestamp candles
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 9: Time-alignment makes the result depend only on common-timestamp candles
@settings(max_examples=100, deadline=None)
@given(data=alignment_inputs())
def test_property_9_time_alignment_depends_only_on_common_timestamps(data):
    """Feature: relative-strength-context, Property 9: Time-alignment makes the
    result depend only on common-timestamp candles — ``time_align`` yields two
    equal-length lists over exactly the common timestamps in ascending order, and
    adding/removing candles at non-common timestamps does not change the result.

    Validates: Requirements 3.7
    """
    sym_full, bench_full = data["sym_full_seq"], data["bench_full_seq"]

    sym_rows, bench_rows = time_align(sym_full, bench_full)

    # The aligned timestamps are exactly the common timestamps, in ascending order.
    expected_ts = sorted(float(ts) for ts in data["common_ts"])

    # Equal-length lists (R3.7).
    assert len(sym_rows) == len(bench_rows) == len(expected_ts)

    sym_ts = [row[0] for row in sym_rows]
    bench_ts = [row[0] for row in bench_rows]

    # Both projections carry exactly the common timestamps, ascending.
    assert sym_ts == expected_ts
    assert bench_ts == expected_ts
    assert sym_ts == sorted(sym_ts)  # strictly ascending (distinct timestamps)

    # Symbol rows are sourced from symbol candles and benchmark rows from
    # benchmark candles (closes match the originating sequence's candle).
    for row in sym_rows:
        ts = int(row[0])
        assert row[4] == data["sym_common"][ts]["close"]
    for row in bench_rows:
        ts = int(row[0])
        assert row[4] == data["bench_common"][ts]["close"]

    # Invariant: the result depends ONLY on common-timestamp candles. Aligning
    # the common-only sequences (no non-common candles at all) yields an
    # identical result — so adding/removing candles at non-common timestamps
    # cannot change the aligned output.
    base_sym, base_bench = time_align(data["sym_common_seq"], data["bench_common_seq"])
    assert base_sym == sym_rows
    assert base_bench == bench_rows
