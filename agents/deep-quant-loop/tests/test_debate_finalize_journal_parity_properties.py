"""Property test for finalize-and-journal parity (multi-agent-debate, task 8.6).

# Feature: multi-agent-debate, Property 14: Committed debate decisions are finalized and journaled like single-agent decisions

Property 14 (Validates: Requirements 5.5):

    For ANY Judge-committed decision, ``_finalize_decision`` attaches a
    defensibility record and invokes ``journal.record_decision``, exactly as for
    a single-agent decision.

``graph._finalize_decision(state, decision)`` is the single chokepoint used by
both the single-agent finalize path and the DEBATE-mode ``judge_node`` finalize
path. It (1) sets ``decision["defensibility"] = build_defensibility_record(...)``
and (2) calls ``journal.record_decision(decision, ...)`` inside a try/except.

This test proves parity: a DEBATE-mode decision and a FIND-mode decision both go
through the SAME path — each attaches a ``defensibility`` dict to the decision
and invokes ``journal.record_decision`` exactly once with that decision. That
identical behaviour IS the parity.

``journal.record_decision`` is the only side-effecting call, so it is replaced
with an in-memory spy for the duration of each example (and restored in a
``finally`` block) to avoid real DB writes. ``build_defensibility_record`` is
pure (it only reads ``messages`` and the optional ``decision["_debate"]``), so it
runs unmodified.
"""

import json
import os
import sys

from hypothesis import given, settings, strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import _finalize_decision  # noqa: E402


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a LangChain ToolMessage tool result.

    ``build_defensibility_record`` reads only ``.type`` (must be ``"tool"``),
    ``.name`` (the originating Analysis_Tool) and ``.content`` (serialized JSON
    tool result), so this minimal stub is sufficient.
    """

    def __init__(self, name, payload):
        self.name = name
        self.content = payload if isinstance(payload, str) else json.dumps(payload)
        self.type = "tool"


# ── Recording spy for journal.record_decision ────────────────────────────────
class RecordSpy:
    """Records every ``record_decision`` invocation; performs no DB write."""

    def __init__(self):
        self.calls = []  # list of (args, kwargs)

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


# ── Generators ────────────────────────────────────────────────────────────────
_FINITE = dict(allow_nan=False, allow_infinity=False)

_actions = st.sampled_from(["BUY", "SELL", "HOLD"])

# A small set of realistic tool-result messages so build_defensibility_record
# has something to read on some examples (and an empty list on others).
_tool_messages = st.lists(
    st.one_of(
        st.fixed_dictionaries(
            {"name": st.just("get_multi_tf_trend"),
             "payload": st.fixed_dictionaries({"1D": st.sampled_from(["Bullish", "Bearish", "Neutral"])})}
        ),
        st.fixed_dictionaries(
            {"name": st.just("get_consensus_report"),
             "payload": st.fixed_dictionaries({"atr_14": st.floats(min_value=0.01, max_value=50.0, **_FINITE)})}
        ),
    ),
    max_size=3,
).map(lambda specs: [StubToolMessage(s["name"], s["payload"]) for s in specs])


@st.composite
def _levels(draw):
    """Optional execution levels (entry/stop_loss/take_profit) or None."""
    if draw(st.booleans()):
        return None
    entry = draw(st.floats(min_value=1.0, max_value=1000.0, **_FINITE))
    stop = draw(st.floats(min_value=0.5, max_value=999.0, **_FINITE))
    tp = draw(st.floats(min_value=1.0, max_value=2000.0, **_FINITE))
    return {"entry": entry, "stop_loss": stop, "take_profit": tp}


@st.composite
def _decision(draw, mode):
    """An arbitrary committed decision. For DEBATE mode it may also carry the
    private ``_debate`` carrier the judge attaches before finalize."""
    action = draw(_actions)
    dec = {
        "action": action,
        "conviction": draw(st.floats(min_value=0.0, max_value=1.0, **_FINITE)),
        "rationale": draw(st.text(max_size=40)),
    }
    levels = draw(_levels())
    if levels is not None:
        dec.update(levels)
    if mode == "DEBATE" and draw(st.booleans()):
        dec["_debate"] = {
            "bull_stance": {"bias": "Bullish", "conviction": 0.6},
            "bear_stance": {"bias": "Bearish", "conviction": 0.4},
            "verdict": draw(st.sampled_from(["strong_agree", "lean", "contested"])),
            "conviction": draw(st.floats(min_value=0.0, max_value=1.0, **_FINITE)),
        }
    return dec


@st.composite
def _state(draw, mode):
    return {
        "messages": draw(_tool_messages),
        "mode": mode,
        "symbol": draw(st.sampled_from(["AAPL", "NIFTY", "BTCUSD", "TSLA"])),
        "timeframe": draw(st.sampled_from(["5m", "15m", "1h", "1d"])),
    }


def _run_finalize(state, decision):
    """Patch journal.record_decision with a spy, run _finalize_decision, and
    return the spy. Restores the original in a finally block."""
    spy = RecordSpy()
    original = graph.journal.record_decision
    graph.journal.record_decision = spy
    try:
        _finalize_decision(state, decision)
    finally:
        graph.journal.record_decision = original
    return spy


# ── Property 14 ───────────────────────────────────────────────────────────────
@settings(max_examples=100)
@given(
    debate_decision=_decision("DEBATE"),
    debate_state=_state("DEBATE"),
    find_decision=_decision("FIND"),
    find_state=_state("FIND"),
)
def test_finalize_and_journal_parity(debate_decision, debate_state, find_decision, find_state):
    """A DEBATE-mode decision and a FIND-mode decision go through the SAME
    finalize chokepoint: both attach a ``defensibility`` dict and invoke
    ``journal.record_decision`` exactly once with the decision."""

    # ── DEBATE-mode finalize ─────────────────────────────────────────────────
    debate_spy = _run_finalize(debate_state, debate_decision)

    assert isinstance(debate_decision.get("defensibility"), dict), (
        "DEBATE finalize must attach a defensibility dict to the decision"
    )
    assert len(debate_spy.calls) == 1, (
        "DEBATE finalize must invoke journal.record_decision exactly once"
    )
    d_args, d_kwargs = debate_spy.calls[0]
    assert d_args and d_args[0] is debate_decision, (
        "DEBATE finalize must journal the same decision object"
    )

    # ── FIND-mode (single-agent) finalize ────────────────────────────────────
    find_spy = _run_finalize(find_state, find_decision)

    assert isinstance(find_decision.get("defensibility"), dict), (
        "FIND finalize must attach a defensibility dict to the decision"
    )
    assert len(find_spy.calls) == 1, (
        "FIND finalize must invoke journal.record_decision exactly once"
    )
    f_args, f_kwargs = find_spy.calls[0]
    assert f_args and f_args[0] is find_decision, (
        "FIND finalize must journal the same decision object"
    )

    # ── Parity: identical observable behaviour on both paths ─────────────────
    # Both attached a defensibility dict and called record_decision exactly once.
    assert len(debate_spy.calls) == len(find_spy.calls) == 1
    # The journaled decision's mode is carried through symbol/timeframe/mode
    # kwargs on both paths (same chokepoint surface).
    assert d_kwargs.get("mode") == "DEBATE"
    assert f_kwargs.get("mode") == "FIND"
    for key in ("symbol", "timeframe"):
        assert key in d_kwargs and key in f_kwargs, (
            f"both paths must pass {key} to journal.record_decision"
        )
