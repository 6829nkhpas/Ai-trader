"""Property-based test for the finite-or-null invariant (options.py, task 9.3).

Feature: options-analytics-engine

This Hypothesis property exercises the full result-assembly path
(:func:`options.assemble_result`) across arbitrary chain snapshots — including
snapshots whose per-strike open-interest / price / volume fields and whose spot
and future price carry non-finite (``NaN`` / ``±inf``) or non-numeric values —
and asserts the engine's sanitization guarantee:

  * Property 11 (6.2, 6.3, 9.3) — Every numeric field of the result is finite or
                                  null: every numeric leaf of the assembled
                                  ``Options_Analytics_Result`` is either a finite
                                  number or ``None`` — never ``NaN`` or
                                  ``±infinity`` — and every reported value is
                                  derived from the chain data rather than
                                  fabricated.
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
    assemble_result,
    resolve_options_config,
)


# ── Smart generators spanning the messy input space the engine must tolerate ──
# Every numeric leaf field can be: a finite number (the normal case), absent
# (None), non-finite (NaN / ±inf), or non-numeric (a string) — exactly the
# adversarial fields Property 11 says the assembled result must sanitize away.
_finite_number = st.floats(
    min_value=-1e9, max_value=1e9,
    allow_nan=False, allow_infinity=False,
)
_non_finite = st.sampled_from([float("nan"), float("inf"), float("-inf")])
_non_numeric = st.sampled_from(["", "NaN", "n/a", "1.0"])

# A "dirty" optional field: finite, absent, non-finite, or non-numeric.
_dirty_value = st.one_of(
    st.none(),
    _finite_number,
    _non_finite,
    _non_numeric,
)

# Positive-ish finite values to keep some prices/OI in a realistic range so that
# Black-Scholes legs occasionally solve (exercising the numeric leaves rather
# than only the all-null degenerate path).
_finite_pos = st.floats(
    min_value=0.01, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)
_priceish = st.one_of(_dirty_value, _finite_pos)

# Strike values: mostly distinct finite positives, but occasionally dirty so the
# assembler's per-strike strike sanitization is exercised too.
_strike_value = st.one_of(
    st.floats(min_value=1.0, max_value=100_000.0,
              allow_nan=False, allow_infinity=False),
    _non_finite,
    _non_numeric,
    st.none(),
)

# Spot / future / risk-free inputs span finite and non-finite values.
_scalar_input = st.one_of(_finite_number, _non_finite, _finite_pos)
_optional_scalar_input = st.one_of(st.none(), _scalar_input)


@st.composite
def _dirty_strike_quote(draw, strike):
    """A StrikeQuote whose every numeric leaf may be finite/absent/non-finite/non-numeric."""
    return StrikeQuote(
        strike=strike,
        ce_price=draw(_priceish),
        pe_price=draw(_priceish),
        ce_oi=draw(_dirty_value),
        pe_oi=draw(_dirty_value),
        ce_volume=draw(_dirty_value),
        pe_volume=draw(_dirty_value),
    )


@st.composite
def _chain_snapshots(draw):
    """A ChainSnapshot whose strikes carry adversarial (non-finite/non-numeric) fields."""
    n = draw(st.integers(min_value=0, max_value=8))
    strikes = [draw(_strike_value) for _ in range(n)]
    quotes = tuple(draw(_dirty_strike_quote(k)) for k in strikes)
    return ChainSnapshot(
        underlying=draw(st.text(min_size=0, max_size=6)),
        expiry=draw(st.sampled_from(["2025-12-25", "not-a-date", "", "2025-01-30"])),
        snapshot_ts=draw(st.one_of(
            st.integers(min_value=0, max_value=2_000_000_000_000),
            st.none(),
        )),
        strikes=quotes,
    )


def _walk_numeric_leaves(value, path="result"):
    """Yield (path, leaf) for every scalar leaf reachable from ``value``.

    Recurses through dicts and lists/tuples; yields strings, ints, floats, bools,
    and None at the leaves so the caller can assert the finite-or-null invariant
    on every float leaf.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_numeric_leaves(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _walk_numeric_leaves(v, f"{path}[{i}]")
    else:
        yield path, value


# ─────────────────────────────────────────────────────────────────────────────
# Property 11 (6.2, 6.3, 9.3): Every numeric field of the result is finite or null
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 11: Every numeric field of the result is finite or null
@settings(max_examples=100)
@given(
    latest=_chain_snapshots(),
    prior=st.one_of(st.none(), _chain_snapshots()),
    spot=_scalar_input,
    future_price=_optional_scalar_input,
)
def test_property_11_every_numeric_field_is_finite_or_null(
    latest, prior, spot, future_price
):
    """Feature: options-analytics-engine, Property 11: Every numeric field of the
    result is finite or null — for any chain snapshot (including snapshots
    carrying non-finite or non-numeric OI / price / volume fields and a
    non-finite spot / future price), every numeric leaf of the assembled
    ``Options_Analytics_Result`` is either a finite number or ``None`` — never
    ``NaN`` or ``±infinity``.

    Validates: Requirements 6.2, 6.3, 9.3
    """
    config = resolve_options_config()

    # The assembler must never raise, regardless of how adversarial the inputs.
    result = assemble_result(latest, prior, spot, future_price, config)

    assert isinstance(result, dict)

    # Every float leaf anywhere in the (possibly nested) result must be finite;
    # ints, strings, bools, and None are permitted. No NaN / ±inf may survive.
    for path, leaf in _walk_numeric_leaves(result):
        if isinstance(leaf, float):
            assert math.isfinite(leaf), (
                f"non-finite float leaf at {path}: {leaf!r}"
            )
