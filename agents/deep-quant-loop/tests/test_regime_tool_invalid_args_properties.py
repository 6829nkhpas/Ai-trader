"""Property-based test for invalid-argument rejection (tools.py, task 5.4).

Feature: regime-detection-gate

This Hypothesis property exercises Property 10 (the regime tool rejects invalid
arguments WITHOUT raising). The ``get_market_regime`` tool validates its
arguments up front: an empty / whitespace-only ``symbol`` or a ``timeframe`` not
in the supported set (``{"1m","5m","10m","15m","1h","4h","1d"}``) is turned into
a STRUCTURED error result — a dict carrying an ``"error"`` key — and the call
never raises.

Crucially, both invalid-argument paths short-circuit BEFORE any candle fetch, so
this property is fully self-contained: it requires no live Rust Tool_Server,
QuestDB, or network access. The generators below deliberately produce ONLY
invalid combinations — empty/whitespace symbols (which short-circuit on the
symbol check) and/or unsupported timeframe strings (which short-circuit on the
timeframe check) — and never a valid-symbol + valid-timeframe pair that would
trigger a candle fetch.

  * Property 10 (3.3) — For any whitespace-only/empty symbol, or any timeframe
    not in the supported set, ``get_market_regime`` returns a structured error
    result (a dict containing an ``"error"`` key) and never raises.
"""

import os
import sys

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


# ─────────────────────────────────────────────────────────────────────────────
# Generators: ONLY invalid (symbol, timeframe) combinations, so every call
# short-circuits on argument validation before any candle fetch.
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


@st.composite
def _invalid_args(draw):
    """Draw a (symbol, timeframe) pair guaranteed to be invalid.

    Two disjoint families, both short-circuiting before any network call:
      * empty/whitespace symbol + any timeframe (symbol check fires first), or
      * any non-empty symbol + unsupported timeframe (timeframe check fires).
    """
    if draw(st.booleans()):
        return draw(_EMPTY_SYMBOL), draw(_ANY_TIMEFRAME)
    return draw(_ANY_SYMBOL), draw(_BAD_TIMEFRAME)


# ─────────────────────────────────────────────────────────────────────────────
# Property 10: invalid arguments -> structured error, never raises
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 10
@settings(max_examples=200, deadline=None)
@given(args=_invalid_args())
def test_property_10_invalid_args_rejected_without_raising(args):
    """Validates: Requirements 3.3

    For any whitespace-only/empty symbol, or any timeframe not in the supported
    set, ``get_market_regime`` returns a structured error result (a dict with an
    ``"error"`` key) and never raises. Both invalid paths short-circuit before
    any candle fetch, so no Rust server / network is required.
    """
    symbol, timeframe = args

    # Sanity: the generated pair really is invalid (so a structured error — not a
    # network fetch — is the expected outcome).
    symbol_invalid = not isinstance(symbol, str) or not symbol.strip()
    timeframe_invalid = timeframe not in SUPPORTED_TIMEFRAMES
    assert symbol_invalid or timeframe_invalid

    # The call must not raise — any exception escaping here fails the property.
    result = _raw(tools.get_market_regime)(symbol=symbol, timeframe=timeframe)

    # The result is a structured error dict carrying an "error" key.
    assert isinstance(result, dict), f"expected a dict result, got {type(result)!r}"
    assert "error" in result, f"expected an 'error' key in {result!r}"
    assert isinstance(result["error"], str) and result["error"]

    # A pure argument-rejection is NOT an Unavailable_Marker (that marker is
    # reserved for retrieval/processing degradation, not bad input).
    assert "unavailable" not in result
