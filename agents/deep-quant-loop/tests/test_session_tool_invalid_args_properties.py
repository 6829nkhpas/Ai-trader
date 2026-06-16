# Feature: session-expiry-awareness, Property 10: The tool rejects invalid arguments without raising
"""Property-based test for session-tool invalid-argument rejection (tools.py, task 4.3).

Feature: session-expiry-awareness

This module implements design **Property 10: The tool rejects invalid arguments
without raising**:

    For any whitespace-only or empty ``symbol``, or any ``timeframe`` not in the
    supported timeframe set, ``get_session_context`` returns a structured error
    result and never raises an exception.

The ``get_session_context`` tool validates its arguments up front (R4.3): an
empty / whitespace-only ``symbol`` or a ``timeframe`` not in
``SUPPORTED_TIMEFRAMES`` (``{"1m","5m","10m","15m","1h","4h","1d"}``) is turned
into a STRUCTURED error result — a dict carrying an ``"error"`` key — and the
call never raises. Both invalid-argument paths short-circuit BEFORE any candle
fetch, so this property is fully self-contained and requires no live Rust
Tool_Server, QuestDB, or network access. As an extra guard the candle fetch
(``tools.httpx.post``) is patched so it fails loudly if it is ever reached on the
invalid-argument paths, proving validation short-circuits before any network
call.

Because ``get_session_context`` is a LangChain ``@tool``-decorated object, it is
invoked here via the undecorated function behind it (``.func``). The sys.path /
import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_rs_tool_invalid_args_properties.py`` and
``tests/test_regime_tool_invalid_args_properties.py``.

Validates: Requirements 4.3
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
    """Stand-in for ``httpx.post`` that fails if any network call is attempted."""
    raise AssertionError(
        "get_session_context performed a network fetch on invalid arguments; "
        "argument validation must short-circuit before any candle fetch"
    )


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
    """Draw a (symbol, timeframe) tuple whose pair is guaranteed invalid.

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

# Feature: session-expiry-awareness, Property 10: The tool rejects invalid arguments without raising
@settings(max_examples=200, deadline=None)
@given(args=_invalid_args())
def test_property_10_session_tool_rejects_invalid_args_without_raising(args):
    """Feature: session-expiry-awareness, Property 10: The tool rejects invalid
    arguments without raising.

    For any whitespace-only/empty symbol, or any timeframe not in the supported
    set, ``get_session_context`` returns a structured error result (a dict with
    an ``"error"`` key) and never raises. Both invalid paths short-circuit before
    any candle fetch, so ``httpx.post`` is patched to fail loudly if reached.

    Validates: Requirements 4.3
    """
    symbol, timeframe = args

    # Sanity: the generated pair really is invalid (so a structured error — not a
    # network fetch — is the expected outcome).
    symbol_invalid = not isinstance(symbol, str) or not symbol.strip()
    timeframe_invalid = timeframe not in SUPPORTED_TIMEFRAMES
    assert symbol_invalid or timeframe_invalid

    # Guard: any network fetch on invalid arguments fails the property.
    with mock.patch.object(tools.httpx, "post", side_effect=_no_network):
        # The call must not raise — any exception escaping here fails the property.
        result = _raw(tools.get_session_context)(symbol=symbol, timeframe=timeframe)

    # The result is a structured error dict carrying an "error" key.
    assert isinstance(result, dict), f"expected a dict result, got {type(result)!r}"
    assert "error" in result, f"expected an 'error' key in {result!r}"
    assert isinstance(result["error"], str) and result["error"]

    # A pure argument-rejection is NOT an Unavailable_Marker (that marker is
    # reserved for retrieval/processing degradation, not bad input), and it
    # never fabricates session fields.
    assert "unavailable" not in result
    assert "session_phase" not in result
    assert "time_favorability" not in result


# ─────────────────────────────────────────────────────────────────────────────
# Companion check: a VALID (symbol, timeframe) with a mocked candle does NOT
# produce a structured error — confirming the rejection above is specific to
# invalid arguments and not a blanket failure.
# ─────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for an ``httpx`` response carrying a single candle."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):  # noqa: D401 - no-op success
        return None

    def json(self):
        return self._payload


# A finite epoch-millisecond timestamp (2021-06-15 ~04:00 UTC -> in IST session).
_VALID_TS_MS = 1_623_727_800_000


def _fake_post(*args, **kwargs):
    """Return one well-formed candle so the valid path classifies successfully."""
    return _FakeResponse([{"timestamp_ms": _VALID_TS_MS}])


# Feature: session-expiry-awareness, Property 10: The tool rejects invalid arguments without raising
@settings(max_examples=100, deadline=None)
@given(
    symbol=st.sampled_from(["RELIANCE", "TCS", "INFY"]),
    timeframe=st.sampled_from(sorted(SUPPORTED_TIMEFRAMES)),
)
def test_property_10_valid_args_with_mocked_candle_do_not_error(symbol, timeframe):
    """A valid (symbol, timeframe) with a mocked candle fetch does NOT return a
    structured argument error, confirming the rejection is specific to invalid
    arguments (Requirements 4.3).
    """
    with mock.patch.object(tools.httpx, "post", side_effect=_fake_post):
        result = _raw(tools.get_session_context)(symbol=symbol, timeframe=timeframe)

    assert isinstance(result, dict)
    # The valid path is not an argument-rejection error result.
    assert "error" not in result
