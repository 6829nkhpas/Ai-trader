"""Property-based test for unmapped-symbol benchmark resolution (rs.py, task 1.5).

Feature: relative-strength-context

This Hypothesis property exercises the Benchmark_Map fallback in
:func:`rs.resolve_benchmark` across the symbol input space. It asserts the
universal invariant of Requirement 2.2: any symbol that has no explicit
Benchmark_Map entry resolves to the documented default Benchmark_Index
(``rs.DEFAULT_BENCHMARK``), provided no explicit benchmark argument and no
``RS_DEFAULT_BENCHMARK`` / ``RS_BENCHMARK_MAP`` environment override is supplied.

  * Property 33 (2.2) — unmapped symbols resolve to the default benchmark.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
from rs import (  # noqa: E402
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_MAP,
    ENV_RS_BENCHMARK_MAP,
    ENV_RS_DEFAULT_BENCHMARK,
    resolve_benchmark,
)

# The two Benchmark_Map env vars the resolver reads. We clear both inside the
# isolation context so the documented defaults (DEFAULT_BENCHMARK /
# DEFAULT_BENCHMARK_MAP) are the sole influence on resolution.
_ALL_BENCHMARK_ENV_VARS = (ENV_RS_DEFAULT_BENCHMARK, ENV_RS_BENCHMARK_MAP)


@contextmanager
def _benchmark_env(overrides):
    """Isolate ``os.environ`` for the benchmark resolver.

    Removes every RS benchmark env var, applies ``overrides``, and restores the
    prior environment exactly on exit (so Hypothesis re-runs never leak state).
    Used instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the
    test body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_BENCHMARK_ENV_VARS}
    try:
        for name in _ALL_BENCHMARK_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


# Symbols spanning realistic tickers plus arbitrary text. We constrain to
# non-empty, non-whitespace strings so each example exercises a genuine symbol
# lookup, then drop any value that (case-insensitively) matches a documented
# DEFAULT_BENCHMARK_MAP entry so the strategy only yields UNMAPPED symbols.
_unmapped_symbol = (
    st.one_of(
        st.text(
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
            min_size=1,
            max_size=12,
        ),
        st.sampled_from(
            ["RELIANCE", "TCS", "INFY", "WIPRO", "ITC", "TATAMOTORS", "ZZZ", "foo"]
        ),
    )
    .filter(lambda s: s.strip() != "")
    .filter(lambda s: s.strip().upper() not in DEFAULT_BENCHMARK_MAP)
)


@settings(max_examples=100)
@given(symbol=_unmapped_symbol)
def test_property_33_unmapped_symbols_resolve_to_default_benchmark(symbol):
    # Feature: relative-strength-context, Property 33: Unmapped symbols resolve to the default benchmark
    """Feature: relative-strength-context, Property 33: Unmapped symbols resolve
    to the default benchmark — a symbol with no explicit Benchmark_Map entry,
    no explicit ``benchmark`` argument, and no environment override resolves to
    the documented default Benchmark_Index (``rs.DEFAULT_BENCHMARK``).

    Validates: Requirements 2.2
    """
    # Sanity: the generated symbol is genuinely unmapped (case-insensitive).
    assert symbol.strip().upper() not in DEFAULT_BENCHMARK_MAP

    with _benchmark_env({}):
        # No explicit benchmark (None / omitted) and no env override => default.
        resolved = resolve_benchmark(symbol)
        resolved_explicit_none = resolve_benchmark(symbol, None)

    assert resolved == DEFAULT_BENCHMARK
    assert resolved_explicit_none == DEFAULT_BENCHMARK
