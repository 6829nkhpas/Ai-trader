"""Property-based test for order-flow invalid-argument rejection (tools.py, task 6.5).

Feature: order-flow-context

This Hypothesis property exercises **Property 16: The tool rejects invalid
arguments without raising**. The ``get_order_flow`` tool validates its arguments
up front: an empty / whitespace-only ``symbol`` or a ``timeframe`` not in the
supported set (``{"1m","5m","10m","15m","1h","4h","1d"}``) is turned into a
STRUCTURED error result — a dict carrying an ``"error"`` key — and the call never
raises (Requirements 5.3).

Both invalid-argument paths short-circuit BEFORE any candle fetch or live-tick
read, so this property is fully self-contained: it requires no live Rust
Tool_Server, QuestDB, or network access. As an extra guard the test patches both
``tools.httpx.post`` (the candle fetch) and ``tools.httpx.get`` (the live_ticks
read) to fail loudly if either is ever reached, proving the validation
short-circuits before any network call. The generators below deliberately
produce ONLY invalid combinations — empty/whitespace symbols (which
short-circuit on the symbol check) and/or unsupported timeframe strings (which
short-circuit on the timeframe check) — and never a valid-symbol + valid-timeframe
pair that would trigger a fetch.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_rs_tool_invalid_args_properties.py`` and
``tests/test_regime_tool_invalid_args_properties.py``.
"""

import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import SUPPORTED_TIMEFRAMES  # noqa: E402


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _no_network(*args, **kwargs):
    """Stand-in for an HTTP call that fails if any network access is attempted."""
    raise AssertionError(
        "get_order_flow performed a network fetch on invalid arguments; "
        "argument validation must short-circuit before any candle fetch or "
        "live_ticks read"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Generators: ONLY invalid (symbol, timeframe) combinations, so every call
# short-circuits on argument validation before any candle fetch / tick read.
# ─────────────────────────────────────────────────────────────────────────────

# Empty / whitespace-only symbols — these short-circuit on the symbol check
# regardless of the accompanying timeframe.
_EMPTY_SYMBOL = st.one_of(
    st.just(""),
    # Whitespace built from spaces, tabs, newlines, etc. (``.strip()`` -> "").
    st.text(alphabet=" \t\n\r\v\f", min_size=1, max_size=8),
    st.sampled_from(["", " ", "   ", "\t", "\n", " \t \n ", "\u00a0 \t"]),
)

# Arbitrary non-empty symbols — paired ONLY with an unsupported timeframe so the
# combination is still invalid (short-circuits on the timeframe check).
_ANY_SYMBOL = st.one_of(
    st.sampled_from(["RELIANCE", "TCS", "INFY", "AAPL", "x", "BTC-USD"]),
    st.text(min_size=1, max_size=12).filter(lambda s: bool(s.strip())),
)

# Timeframes NOT in the supported set. Includes near-misses (case variants,
# plausible-but-unsupported intervals) and free-form text excluding the valid
# values so the unsupported branch is stressed broadly.
_BAD_TIMEFRAME = st.one_of(
    st.sampled_from(
        ["", " ", "2m", "3m", "30m", "45m", "2h", "6h", "12h", "1w", "1M",
         "1D", "1H", "5M", "15M", "60m", "1d ", " 1d", "day", "daily", "tick"]
    ),
    st.text(max_size=10).filter(lambda t: t not in SUPPORTED_TIMEFRAMES),
)

# Any timeframe at all (valid or not) — only ever paired with an empty symbol,
# where the symbol check fires first.
_ANY_TIMEFRAME = st.one_of(
    st.sampled_from(sorted(SUPPORTED_TIMEFRAMES)),
    _BAD_TIMEFRAME,
)

# Optional proposed_direction argument — varied to show invalid (symbol,
# timeframe) pairs are rejected irrespective of the other arguments.
_PROPOSED_DIRECTION = st.sampled_from(["", "BUY", "SELL", "HOLD", "buy", "garbage"])


@st.composite
def _invalid_args(draw):
    """Draw a (symbol, timeframe, proposed_direction) tuple whose
    (symbol, timeframe) pair is guaranteed invalid.

    Two disjoint families, both short-circuiting before any network call:
      * empty/whitespace symbol + any timeframe (symbol check fires first), or
      * any non-empty symbol + unsupported timeframe (timeframe check fires).
    """
    if draw(st.booleans()):
        symbol, timeframe = draw(_EMPTY_SYMBOL), draw(_ANY_TIMEFRAME)
    else:
        symbol, timeframe = draw(_ANY_SYMBOL), draw(_BAD_TIMEFRAME)
    return symbol, timeframe, draw(_PROPOSED_DIRECTION)


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: invalid arguments -> structured error, never raises
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 16: The tool rejects invalid arguments without raising
@settings(max_examples=200, deadline=None)
@given(args=_invalid_args())
def test_property_16_of_tool_rejects_invalid_args_without_raising(args):
    """Feature: order-flow-context, Property 16: The tool rejects invalid
    arguments without raising.

    For any whitespace-only/empty symbol, or any timeframe not in the supported
    set, ``get_order_flow`` returns a structured error result (a dict with an
    ``"error"`` key) and never raises. Both invalid paths short-circuit before
    any candle fetch or live_ticks read, so ``httpx.post`` and ``httpx.get`` are
    patched to fail loudly if either is reached.

    Validates: Requirements 5.3
    """
    symbol, timeframe, proposed_direction = args

    # Sanity: the generated pair really is invalid (so a structured error — not a
    # network fetch — is the expected outcome).
    symbol_invalid = not isinstance(symbol, str) or not symbol.strip()
    timeframe_invalid = timeframe not in SUPPORTED_TIMEFRAMES
    assert symbol_invalid or timeframe_invalid

    # Guard: any network access on invalid arguments fails the property — the
    # candle fetch uses httpx.post, the live_ticks read uses httpx.get.
    with mock.patch.object(tools.httpx, "post", side_effect=_no_network), \
            mock.patch.object(tools.httpx, "get", side_effect=_no_network):
        # The call must not raise — any exception escaping here fails the property.
        result = _raw(tools.get_order_flow)(
            symbol=symbol,
            timeframe=timeframe,
            proposed_direction=proposed_direction,
        )

    # The result is a structured error dict carrying an "error" key.
    assert isinstance(result, dict), f"expected a dict result, got {type(result)!r}"
    assert "error" in result, f"expected an 'error' key in {result!r}"
    assert isinstance(result["error"], str) and result["error"]

    # A pure argument-rejection is NOT an Unavailable_Marker (that marker is
    # reserved for retrieval/processing degradation, not bad input).
    assert "unavailable" not in result
