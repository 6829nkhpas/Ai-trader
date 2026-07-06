"""Property-based test for options chain resolution and labelling
(tools.py, task 4.4).

Feature: options-agent-integration

This Hypothesis property exercises the ``get_options_analytics`` tool in
``tools.py`` with the F2 analytics engine MOCKED. It covers design **Property 7:
Chain resolution is correct and always labelled**: for ANY symbol, the tool
analyzes the symbol's OWN chain with ``chain_context="own-chain"`` when the
symbol is an index Underlying (a member of ``INDEX_UNDERLYINGS``, matched
case-insensitively, e.g. ``"NIFTY 50"`` / ``"NIFTY"`` / ``"BANKNIFTY"``), and
otherwise analyzes the symbol's ``Benchmark_Index`` chain
(``underlying == rs.resolve_benchmark(symbol)``) with
``chain_context="broad-market"``; ``chain_context`` is ALWAYS present and one of
those two labels.

The single I/O dependency — the F2 engine ``options.compute_options_analytics``
— is monkeypatched at the module level to ECHO BACK the ``underlying`` it is
called with (plus a well-formed usable analytics body), so the full tool path
runs (arg validation -> chain resolution + label -> analytics -> config
resolution -> classify -> merge -> contract re-validation) with NO live QuestDB
and NO F2 read layer, and the merged result's ``underlying`` reflects exactly the
chain the tool resolved. ``rs.resolve_benchmark`` is used AS-IS (it is pure: it
reads only env vars and never performs I/O). The monkeypatched attribute is
restored in a ``finally`` block.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_options_tool_usable_result_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / options.py / rs.py live one
# level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402
import rs  # noqa: E402
from tools import (  # noqa: E402
    INDEX_UNDERLYINGS,
    OPTIONS_CHAIN_CONTEXTS,
    get_options_analytics,
)

# Index symbols (own-chain branch), including case-insensitive and internal-space
# variants — every one of these upper-cases into INDEX_UNDERLYINGS.
_INDEX_SYMBOLS = sorted(INDEX_UNDERLYINGS) + [
    "nifty",
    "NiFtY",
    "banknifty",
    "BankNifty",
    "nifty 50",
    "Nifty 50",
]

# Non-index symbols (broad-market branch) — these resolve to a Benchmark_Index
# via rs.resolve_benchmark (bank names -> BANKNIFTY, otherwise -> NIFTY 50).
_NON_INDEX_SYMBOLS = [
    "RELIANCE",
    "HDFCBANK",
    "TCS",
    "INFY",
    "SBIN",
    "ICICIBANK",
    "TATAMOTORS",
    "AXISBANK",
]

# Proposed directions: empty (neutral), the directional pair, HOLD, lower-case
# variants, and junk — the tool must handle all without raising and the chain
# resolution must be independent of the direction.
_DIRECTIONS = ["", "BUY", "SELL", "HOLD", "buy", "sell", "sideways"]


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


# A finite real number or null (None) — bool excluded by construction.
_num_or_null = st.one_of(
    st.none(),
    st.floats(min_value=-1.0e6, max_value=1.0e6,
              allow_nan=False, allow_infinity=False),
)

# A strictly-positive finite spot price (the system spot the engine resolves).
_positive = st.floats(min_value=1.0, max_value=1.0e6,
                      allow_nan=False, allow_infinity=False)

# An aggregate OI-buildup classification per side (or null / mixed).
_buildup_side = st.sampled_from(
    ["long_buildup", "short_buildup", "long_unwinding", "short_covering",
     "neutral", None]
)


@st.composite
def _analytics_body(draw):
    """A well-formed, usable ``Options_Analytics_Result`` body WITHOUT an
    ``underlying`` key.

    The mock injects ``underlying`` (the exact value the tool passes to the F2
    engine) at call time, so the merged result's ``underlying`` reflects the
    chain the tool resolved — never a value baked into the test fixture.
    """
    iv_skew = draw(
        st.one_of(
            st.none(),
            st.fixed_dictionaries({"put_minus_call": _num_or_null}),
        )
    )
    return {
        "expiry": draw(st.sampled_from(["2024-01-25", "2024-02-29", "", None])),
        "spot": draw(_positive),
        "pcr_oi": draw(_num_or_null),
        "pcr_volume": draw(_num_or_null),
        "max_pain": draw(_num_or_null),
        "oi_buildup": {"call": draw(_buildup_side), "put": draw(_buildup_side)},
        "oi_walls": {"support": draw(_num_or_null), "resistance": draw(_num_or_null)},
        "iv_skew": iv_skew,
        "futures_basis": draw(_num_or_null),
    }


def _run_tool(symbol, proposed_direction, body):
    """Run the tool with the F2 engine mocked to echo back the resolved
    ``underlying``. Returns ``(result, captured_underlying)``."""
    captured = {}

    def _fake_compute(underlying, expiry):
        # Record exactly what chain the tool resolved and asked the engine for,
        # and echo it back so the merged result.underlying reflects it.
        captured["underlying"] = underlying
        return {**body, "underlying": underlying}

    original = options.compute_options_analytics
    try:
        options.compute_options_analytics = _fake_compute
        result = _raw(get_options_analytics)(
            symbol=symbol, proposed_direction=proposed_direction
        )
    finally:
        options.compute_options_analytics = original
    return result, captured.get("underlying")


# ─────────────────────────────────────────────────────────────────────────────
# Property 7: Chain resolution is correct and always labelled
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 7: Chain resolution is correct and always labelled
@settings(max_examples=200, deadline=None)
@given(
    symbol=st.sampled_from(_INDEX_SYMBOLS),
    proposed_direction=st.sampled_from(_DIRECTIONS),
    body=_analytics_body(),
)
def test_property_7_index_symbol_uses_own_chain(symbol, proposed_direction, body):
    """Feature: options-agent-integration, Property 7: Chain resolution is
    correct and always labelled — for any index Underlying, the tool analyzes the
    symbol's OWN chain (underlying == symbol) and labels it chain_context =
    "own-chain", which is always present and one of the two valid labels.

    Validates: Requirements 2.3
    """
    result, engine_underlying = _run_tool(symbol, proposed_direction, body)

    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # chain_context is ALWAYS present and one of the two valid labels.
    assert "chain_context" in result, f"chain_context missing from {result!r}"
    assert result["chain_context"] in OPTIONS_CHAIN_CONTEXTS, (
        f"chain_context {result['chain_context']!r} not in {OPTIONS_CHAIN_CONTEXTS}"
    )

    # An index Underlying uses its OWN chain.
    assert result["chain_context"] == "own-chain", (
        f"index symbol {symbol!r} should resolve to own-chain, "
        f"got {result['chain_context']!r}"
    )

    # The analyzed chain (passed to the F2 engine and merged into the result) is
    # the symbol's own chain (the stripped symbol).
    assert engine_underlying == symbol.strip(), (
        f"index symbol {symbol!r} should analyze its own chain "
        f"{symbol.strip()!r}, but the engine was called with {engine_underlying!r}"
    )
    assert result.get("underlying") == symbol.strip(), (
        f"result.underlying {result.get('underlying')!r} != own chain "
        f"{symbol.strip()!r} for index symbol {symbol!r}"
    )


# Feature: options-agent-integration, Property 7: Chain resolution is correct and always labelled
@settings(max_examples=200, deadline=None)
@given(
    symbol=st.sampled_from(_NON_INDEX_SYMBOLS),
    proposed_direction=st.sampled_from(_DIRECTIONS),
    body=_analytics_body(),
)
def test_property_7_non_index_symbol_uses_benchmark_chain(
    symbol, proposed_direction, body
):
    """Feature: options-agent-integration, Property 7: Chain resolution is
    correct and always labelled — for any non-index symbol, the tool analyzes the
    symbol's Benchmark_Index chain (underlying == rs.resolve_benchmark(symbol))
    and labels it chain_context = "broad-market", which is always present and one
    of the two valid labels.

    Validates: Requirements 2.3
    """
    result, engine_underlying = _run_tool(symbol, proposed_direction, body)

    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # chain_context is ALWAYS present and one of the two valid labels.
    assert "chain_context" in result, f"chain_context missing from {result!r}"
    assert result["chain_context"] in OPTIONS_CHAIN_CONTEXTS, (
        f"chain_context {result['chain_context']!r} not in {OPTIONS_CHAIN_CONTEXTS}"
    )

    # A non-index symbol uses its Benchmark_Index chain as broad-market context.
    assert result["chain_context"] == "broad-market", (
        f"non-index symbol {symbol!r} should resolve to broad-market, "
        f"got {result['chain_context']!r}"
    )

    # The analyzed chain is exactly rs.resolve_benchmark(symbol) (used as-is).
    expected_chain = rs.resolve_benchmark(symbol)
    assert engine_underlying == expected_chain, (
        f"non-index symbol {symbol!r} should analyze benchmark chain "
        f"{expected_chain!r}, but the engine was called with {engine_underlying!r}"
    )
    assert result.get("underlying") == expected_chain, (
        f"result.underlying {result.get('underlying')!r} != benchmark chain "
        f"{expected_chain!r} for non-index symbol {symbol!r}"
    )
