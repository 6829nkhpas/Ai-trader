"""Property-based test for a well-formed successful tool result (tools.py, task 5.7).

Feature: relative-strength-context

This Hypothesis property exercises the ``get_relative_strength`` tool in
``tools.py`` with the candle retrieval MOCKED. It covers design Property 14: for
any symbol / benchmark candle data sufficient to classify (retrieval mocked), a
successful (non-unavailable, non-error) ``get_relative_strength`` result is
well-formed — it carries ``index_direction`` in its enum, ``relative_strength_state``
in its enum, ``alignment`` in its enum, a ``benchmark`` string, and a ``measures``
dict whose five named Relative_Strength_Measures are each present as a finite
number or ``null`` (None).

The tool fetches BOTH the symbol candles and the resolved-benchmark candles via
``httpx.post(f"{RUST_SERVER_URL}/tools/get_candles", ...)`` and reads each with
``response.json()`` (a list of OHLCV candle dicts). Here ``tools.httpx.post`` is
patched to route on the requested ``symbol`` — returning a generated symbol price
walk for the symbol request and a generated benchmark price walk for the
benchmark request — so the test exercises the full tool path (arg validation →
benchmark resolution → config resolution → classify → contract re-validation)
with NO live Rust Tool_Server.

Both series share the SAME ascending timestamps so time-alignment leaves enough
common candles to classify. Some generated walks may still degenerate (e.g. an
all-null measure window) and yield an Unavailable_Marker; those are skipped via
``assume`` because this property asserts only over produced Relative_Strength_Labels.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_rs_tool_fetches_both.py`` and
``tests/test_regime_tool_success_properties.py``.
"""

import json
import math
import os
import sys
from unittest import mock

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
import tools  # noqa: E402
from tools import (  # noqa: E402
    ALIGNMENT_VALUES,
    INDEX_DIRECTIONS,
    RELATIVE_STRENGTH_STATES,
    _RS_MEASURE_FIELDS,
    get_relative_strength,
)

# The default resolved config gates on
# ``max(min_candles=30, largest_lookback)`` where
# ``largest_lookback = max(lookback=20, corr_window=30) + 1 = 31``.
# Generate comfortably more than that many time-aligned candles so the
# classifier reliably produces a Relative_Strength_Label (not Unavailable).
_MIN_CANDLES = 60
_MAX_CANDLES = 90

# A symbol whose Benchmark_Map resolution yields a DIFFERENT benchmark, so the
# two candle fetches are for distinct series (a symbol cannot be its own
# benchmark — the tool degrades that to unavailable).
_SYMBOL = "RELIANCE"


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data, status_code=200):
    """Build a stand-in for an httpx.Response carrying ``json_data``.

    ``.json()`` yields the candle list the tool reads; ``.raise_for_status()`` is
    a no-op so the mocked retrieval looks successful.
    """
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = mock.Mock(return_value=json_data)
    resp.raise_for_status = mock.Mock(return_value=None)
    return resp


@st.composite
def _price_walk(draw, n):
    """A sequence of ``n`` valid OHLCV candle dicts following a random price walk.

    Every candle's OHLCV fields are finite numbers and consecutive closes move,
    so the path has real movement and a real high-low range, keeping measure
    denominators generally non-zero. Timestamps are a fixed ascending grid
    (``i * 1000``) shared by both series so time-alignment retains every candle.
    """
    price = draw(
        st.floats(min_value=10.0, max_value=10_000.0,
                  allow_nan=False, allow_infinity=False)
    )
    candles = []
    for i in range(n):
        step = draw(
            st.floats(min_value=-50.0, max_value=50.0,
                      allow_nan=False, allow_infinity=False)
        )
        new_price = max(price + step, 1.0)
        open_ = price
        close = new_price
        high = max(open_, close) + draw(
            st.floats(min_value=0.0, max_value=10.0,
                      allow_nan=False, allow_infinity=False)
        )
        low = max(
            min(open_, close)
            - draw(
                st.floats(min_value=0.0, max_value=10.0,
                          allow_nan=False, allow_infinity=False)
            ),
            0.5,
        )
        candles.append(
            {
                "timestamp_ms": i * 1000,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0 + i,
            }
        )
        price = new_price
    return candles


@st.composite
def _symbol_and_benchmark_candles(draw):
    """Two time-aligned OHLCV walks (symbol, benchmark) of equal length.

    Sharing one ascending timestamp grid guarantees that time-alignment keeps
    every candle, so the aligned-candle count clears the classifier gate.
    """
    n = draw(st.integers(min_value=_MIN_CANDLES, max_value=_MAX_CANDLES))
    sym_candles = draw(_price_walk(n))
    bench_candles = draw(_price_walk(n))
    return sym_candles, bench_candles


def _is_finite_or_null(value) -> bool:
    """True when ``value`` is None or a finite real number (bool excluded)."""
    if value is None:
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 14: A successful tool result is well-formed
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 14: A successful tool result is well-formed
@settings(max_examples=100, deadline=None)
@given(series=_symbol_and_benchmark_candles())
def test_property_14_successful_tool_result_is_well_formed(series):
    """Feature: relative-strength-context, Property 14: A successful tool result
    is well-formed — for any symbol / benchmark candle data sufficient to
    classify (with retrieval MOCKED), a non-unavailable, non-error
    ``get_relative_strength`` result carries ``index_direction`` in its enum,
    ``relative_strength_state`` in its enum, ``alignment`` in its enum, a
    ``benchmark`` string, and a ``measures`` dict whose five named measures are
    each present as a finite number or null.

    Validates: Requirements 4.5
    """
    sym_candles, bench_candles = series

    # The symbol must resolve to a DISTINCT benchmark (else the tool honestly
    # degrades to unavailable rather than fabricate self-relative strength).
    resolved_benchmark = rs.resolve_benchmark(_SYMBOL)
    assert resolved_benchmark != _SYMBOL

    def _fake_post(url, json=None, timeout=None, **kwargs):
        requested_symbol = (json or {}).get("symbol")
        if requested_symbol == _SYMBOL:
            return _mock_response(sym_candles)
        if requested_symbol == resolved_benchmark:
            return _mock_response(bench_candles)
        raise AssertionError(f"unexpected candle request for {requested_symbol!r}")

    # Mock the candle retrieval so the tool runs against generated data with no
    # live Rust Tool_Server. Routing on the requested symbol feeds the symbol
    # walk to the symbol fetch and the benchmark walk to the benchmark fetch.
    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post):
        result = _raw(get_relative_strength)(
            symbol=_SYMBOL, timeframe="15m", proposed_direction="BUY"
        )

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # A degenerate walk can still yield an Unavailable_Marker (e.g. an all-null
    # measure window) or — should it ever occur — an error; this property
    # asserts only over produced Relative_Strength_Labels.
    assume("unavailable" not in result)
    assume("error" not in result)

    # The three categorical fields must each be drawn from their fixed enums.
    assert result.get("index_direction") in INDEX_DIRECTIONS, (
        f"index_direction {result.get('index_direction')!r} not in {INDEX_DIRECTIONS}"
    )
    assert result.get("relative_strength_state") in RELATIVE_STRENGTH_STATES, (
        f"relative_strength_state {result.get('relative_strength_state')!r} "
        f"not in {RELATIVE_STRENGTH_STATES}"
    )
    assert result.get("alignment") in ALIGNMENT_VALUES, (
        f"alignment {result.get('alignment')!r} not in {ALIGNMENT_VALUES}"
    )

    # The resolved benchmark must be present as a non-empty string.
    bench = result.get("benchmark")
    assert isinstance(bench, str) and bench.strip(), (
        f"benchmark is not a non-empty string: {bench!r}"
    )

    # The measures dict must carry all five named measures, each finite-or-null.
    measures = result.get("measures")
    assert isinstance(measures, dict), f"'measures' is not a dict: {measures!r}"
    for field in _RS_MEASURE_FIELDS:
        assert field in measures, f"measure '{field}' missing from {measures!r}"
        value = measures[field]
        assert _is_finite_or_null(value), (
            f"measure '{field}' is neither a finite number nor null: {value!r}"
        )
