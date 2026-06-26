"""Property-based tests for Put-Call Ratio analytics (options.py, task 4.4).

Feature: options-analytics-engine

This Hypothesis property exercises the two deterministic Put-Call Ratio
aggregates over an in-memory chain snapshot
(:func:`options.compute_pcr_oi` / :func:`options.compute_pcr_volume`) across the
full field-value input space — finite OI/volume, ``None``, ``NaN``, ``±inf``,
and zero-call-OI cases — asserting the put/call-quotient invariant:

  * Property 4 (2.1, 2.2) — Put-Call Ratio is the put/call quotient with null on
                            zero denominator: PCR by OI equals total put OI /
                            total call OI (null exactly when total call OI is
                            zero), and PCR by volume equals total put volume /
                            total call volume (null exactly when total call
                            volume is zero or unavailable). Non-finite fields are
                            excluded from the sums (Requirement 9.3).
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import (  # noqa: E402
    ChainSnapshot,
    StrikeQuote,
    compute_pcr_oi,
    compute_pcr_volume,
)


def _is_finite(x):
    """Mirror options._is_finite: real, finite, non-bool number (zero allowed)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _sum_finite(values):
    """Mirror options._sum_finite: total of only the finite-number entries."""
    return sum(float(v) for v in values if _is_finite(v))


# A single numeric field: spans absent (None), non-finite (NaN/±inf), and finite
# values (including the zero that drives the null-denominator boundary). Used for
# every OI/volume field so chains include the degenerate cases the property must
# tolerate without raising.
_field_value = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.just(0.0),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
)


@st.composite
def _strike_quotes(draw):
    """A StrikeQuote with varied (possibly absent/non-finite) OI and volume."""
    return StrikeQuote(
        strike=draw(st.floats(min_value=0.0, max_value=1e6,
                              allow_nan=False, allow_infinity=False)),
        ce_price=draw(_field_value),
        pe_price=draw(_field_value),
        ce_oi=draw(_field_value),
        pe_oi=draw(_field_value),
        ce_volume=draw(_field_value),
        pe_volume=draw(_field_value),
    )


@st.composite
def _chain_snapshots(draw):
    """A ChainSnapshot with a (possibly empty) ladder of varied strikes."""
    quotes = draw(st.lists(_strike_quotes(), min_size=0, max_size=12))
    return ChainSnapshot(
        underlying=draw(st.text(min_size=0, max_size=6)),
        expiry=draw(st.text(min_size=0, max_size=8)),
        snapshot_ts=draw(st.integers(min_value=0, max_value=2_000_000_000_000)),
        strikes=tuple(quotes),
    )


@settings(max_examples=100)
@given(snapshot=_chain_snapshots())
def test_property_4_pcr_is_put_call_quotient_with_null_on_zero_denominator(snapshot):
    # Feature: options-analytics-engine, Property 4: Put-Call Ratio is the put/call quotient with null on zero denominator
    """Feature: options-analytics-engine, Property 4: Put-Call Ratio is the
    put/call quotient with null on zero denominator — for ANY chain snapshot,
    PCR by OI = total put OI / total call OI (null exactly when total call OI is
    zero) and PCR by volume = total put volume / total call volume (null exactly
    when total call volume is zero or unavailable), counting only finite fields.

    Validates: Requirements 2.1, 2.2
    """
    strikes = snapshot.strikes

    # Expected denominators / numerators counting only finite fields.
    total_call_oi = _sum_finite(q.ce_oi for q in strikes)
    total_put_oi = _sum_finite(q.pe_oi for q in strikes)
    total_call_vol = _sum_finite(q.ce_volume for q in strikes)
    total_put_vol = _sum_finite(q.pe_volume for q in strikes)

    pcr_oi = compute_pcr_oi(snapshot)
    pcr_vol = compute_pcr_volume(snapshot)

    # ── PCR by open interest ─────────────────────────────────────────────────
    if not strikes or total_call_oi == 0.0:
        # Empty ladder or zero call-OI denominator → null (no division by zero).
        assert pcr_oi is None
    else:
        expected_oi = total_put_oi / total_call_oi
        if math.isfinite(expected_oi):
            assert pcr_oi is not None
            assert math.isclose(pcr_oi, expected_oi, rel_tol=1e-9, abs_tol=1e-12)
        else:
            assert pcr_oi is None

    # ── PCR by traded volume ─────────────────────────────────────────────────
    # Zero call volume and "unavailable" (all volumes absent/non-finite) both
    # manifest as a zero finite total, so both degrade to null identically.
    if not strikes or total_call_vol == 0.0:
        assert pcr_vol is None
    else:
        expected_vol = total_put_vol / total_call_vol
        if math.isfinite(expected_vol):
            assert pcr_vol is not None
            assert math.isclose(pcr_vol, expected_vol, rel_tol=1e-9, abs_tol=1e-12)
        else:
            assert pcr_vol is None
