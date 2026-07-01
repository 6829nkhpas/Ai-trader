"""Property-based test for telemetry cost proxies (telemetry.py, task 4.5).

Feature: session-telemetry

This module implements design **Property 7: Cost proxies are consistent counts
and never fabricate tokens**:

    For any observed event stream, ``tool_calls_total`` equals the sum of
    ``tool_calls_by_name.values()`` and each per-tool count equals the number of
    that tool's calls; ``model_turns`` equals the number of model/reasoning turns
    and ``resume_count`` the number of resumes; and ``tokens`` equals the run's
    exposed token usage when present and is ``null`` (never a fabricated number)
    when the run exposes no token usage.

Validates: Requirements 3.3, 3.4.

The cost proxies a Session_Record exposes are, by design, folded straight out of
the ``SessionState`` accumulator the background writer builds up: the writer keeps
``tool_calls_by_name`` as a per-tool tally, ``tool_calls_total`` as the running
sum of those per-tool counts, ``model_turns`` / ``resume_count`` / ``reasoning_turns``
as running counters, and ``tokens`` only when the run exposes a real integer count
(otherwise it stays ``None``). ``finalize_session`` is the sole pure boundary that
turns that accumulator into the immutable record, so this property builds
``SessionState`` accumulators that mirror how the writer populates them — a
per-tool dict with ``tool_calls_total`` equal to the sum of its values — and
asserts ``finalize_session`` preserves every cost proxy exactly and never
fabricates a token count. The sys.path / import pattern mirrors
``tests/test_telemetry_config_robustness_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import (  # noqa: E402
    SessionState,
    finalize_session,
)

# ── Hypothesis strategies over arbitrary cost-proxy accumulators ──────────────
# ``tool_calls_by_name`` is a per-tool tally (tool name -> non-negative count),
# exactly as the writer accumulates it. ``tool_calls_total`` is then the sum of
# those per-tool counts (the writer's maintained invariant). model_turns,
# resume_count and reasoning_turns are running counters.

_tool_names = st.sampled_from(
    ["get_candles", "options_snapshot", "order_flow", "regime_snapshot", "watch_price_condition", "rs_snapshot"]
)

# A per-tool tally: some tools with a positive call count each (an empty dict is
# also allowed to exercise the zero-total degenerate case).
_tool_calls_by_name = st.dictionaries(
    keys=_tool_names,
    values=st.integers(min_value=1, max_value=25),
    max_size=6,
)

_counter = st.integers(min_value=0, max_value=50)

# ``tokens`` is EITHER a real exposed integer count OR ``None`` (not exposed). The
# ``None`` branch exercises the "never fabricate" guarantee (R3.4).
_tokens = st.one_of(st.none(), st.integers(min_value=0, max_value=2_000_000))

_symbol = st.one_of(st.none(), st.sampled_from(["RELIANCE", "INFY", "TCS"]))
_timeframe = st.one_of(st.none(), st.sampled_from(["5m", "15m", "1h"]))
_mode = st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"]))
_ts = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


def _make_state(tool_calls_by_name, model_turns, resume_count, reasoning_turns, tokens, symbol, timeframe, mode, started_at):
    """Build a SessionState accumulator the way the writer would populate it.

    ``tool_calls_total`` is the sum of the per-tool counts — the invariant the
    background writer maintains as it increments both the total and the per-tool
    tally on each TOOL_CALL_START.
    """
    return SessionState(
        thread_id="thread-cost-proxy",
        symbol=symbol,
        timeframe=timeframe,
        mode=mode,
        started_at=started_at,
        tool_calls_total=sum(tool_calls_by_name.values()),
        tool_calls_by_name=dict(tool_calls_by_name),
        model_turns=model_turns,
        resume_count=resume_count,
        reasoning_turns=reasoning_turns,
        tokens=tokens,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 (task 4.5): Cost proxies are consistent counts and never fabricate tokens
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 7: Cost proxies are consistent counts and never fabricate tokens
@settings(max_examples=100, deadline=None)
@given(
    tool_calls_by_name=_tool_calls_by_name,
    model_turns=_counter,
    resume_count=_counter,
    reasoning_turns=_counter,
    tokens=_tokens,
    symbol=_symbol,
    timeframe=_timeframe,
    mode=_mode,
    started_at=_ts,
)
def test_property_7_cost_proxies_consistent_and_no_fabricated_tokens(
    tool_calls_by_name, model_turns, resume_count, reasoning_turns, tokens, symbol, timeframe, mode, started_at
):
    """Feature: session-telemetry, Property 7: Cost proxies are consistent counts
    and never fabricate tokens — for any accumulated cost proxies, the finalized
    Session_Record has ``tool_calls_total`` equal to the sum of
    ``tool_calls_by_name.values()`` with every per-tool count preserved exactly,
    ``model_turns`` / ``resume_count`` carried over unchanged, and ``tokens`` equal
    to the exposed integer count when present and ``None`` (never fabricated) when
    the run exposed no token usage.

    Validates: Requirements 3.3, 3.4
    """
    state = _make_state(
        tool_calls_by_name, model_turns, resume_count, reasoning_turns, tokens, symbol, timeframe, mode, started_at
    )

    record = finalize_session(state)

    # ── R3.3: per-tool counts preserved exactly, total equals their sum ───────
    assert record.tool_calls_by_name == tool_calls_by_name
    assert record.tool_calls_total == sum(record.tool_calls_by_name.values())
    # Each per-tool count equals the number of that tool's calls (the tally value).
    for tool, count in tool_calls_by_name.items():
        assert record.tool_calls_by_name[tool] == count

    # The record must not alias the accumulator's per-tool dict (Requirement 8.4).
    assert record.tool_calls_by_name is not state.tool_calls_by_name

    # ── R3.3: model_turns and resume_count carried over unchanged ─────────────
    assert record.model_turns == model_turns
    assert record.resume_count == resume_count
    assert record.reasoning_turns == reasoning_turns

    # ── R3.4: tokens preserved when exposed, None (never fabricated) when not ──
    if tokens is None:
        assert record.tokens is None
    else:
        assert record.tokens == tokens
        assert isinstance(record.tokens, int)


# Feature: session-telemetry, Property 7: Cost proxies are consistent counts and never fabricate tokens
@settings(max_examples=100, deadline=None)
@given(started_at=_ts)
def test_property_7_unexposed_tokens_never_fabricated(started_at):
    """Feature: session-telemetry, Property 7 (focus on R3.4) — a Session whose
    accumulator never observed a token count finalizes with ``tokens is None``; the
    cost proxy is the model-turn count, and no token number is fabricated.

    Validates: Requirements 3.4
    """
    state = SessionState(
        thread_id="thread-no-tokens",
        started_at=started_at,
        model_turns=7,
        # tokens deliberately left at its default (None): the run exposed none.
    )
    record = finalize_session(state)

    assert record.tokens is None
    # The model-turn count stands in as the cost proxy (R3.4).
    assert record.model_turns == 7
