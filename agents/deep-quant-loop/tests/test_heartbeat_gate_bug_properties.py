"""R6 bug-condition exploration test — heartbeat precipitates a premature stand-aside.

Feature: deep-quant-runtime-hardening (bugfix)

Property 14 (Bug Condition), Python ``graph.tool_node`` finalize seam —
"Heartbeat must not precipitate a premature stand-aside":

    On a heartbeat resume (``last_resume_kind == RESUME_HEARTBEAT``), a
    model-declared ``stand_aside`` HOLD (conviction > 0) must NOT be committed
    as the terminal decision while the CORE data acquisition (regime,
    relative-strength, session, order-flow) is still unresolved (only failing /
    never-returned) and NO bounded-hunt cap has fired. A stand-aside reached in
    that window is an artifact of incomplete acquisition, not a considered
    judgment — the loop must keep gathering the core tools first.

    Validates: Requirements 6.1, 6.2.

*** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***

Root cause (design R6): the existing first-pass gate keys ONLY on
``market_data_seen`` (``graph.py`` ~1385-1404, ~3186-3405), which latches
``True`` as soon as ANY single market-data tool returns usable data. So when
``get_consensus_report`` succeeds but ``get_market_regime`` /
``get_relative_strength`` / ``get_session_context`` / ``get_order_flow`` are
still failing, a heartbeat-woken turn reaches the "normal finalize path" in
``tool_node`` and commits the ``declare_trade`` HOLD verbatim
(``update["decision"] = _decision_from_declare(ok_calls)``). Nothing in the
unfixed routing consults whether the CORE acquisition has resolved or whether
the resume was a heartbeat.

The assertions below encode the CORRECT (fixed) behavior — that such a premature
HOLD is NOT committed and the loop continues — so they FAIL on the unfixed
routing. That failure is the informative, expected outcome: it proves the
premature stand-aside. DO NOT fix the code here; task 14.2 adds the acquisition
gate and task 14.5 re-runs THIS SAME test to confirm the fix.

Concrete documented counterexample (see the first test): on roughly the 2nd
heartbeat wake the agent commits a conviction-74 ``stand_aside`` HOLD while four
core tools (regime, relative-strength, session, order-flow) are still
unresolved and no cap has fired.
"""

import json
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` / ``import opportunity`` resolve as every sibling test expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
import opportunity  # noqa: E402
from graph import tool_node  # noqa: E402


# ── The four CORE data tools whose first-pass acquisition R6 gates ────────────
# (get_consensus_report is the read that already latches market_data_seen True;
# these four are the ones left unresolved in the bug window.)
CORE_TOOLS = (
    "get_market_regime",
    "get_relative_strength",
    "get_session_context",
    "get_order_flow",
)


# ── Lightweight stub message objects (mirror test_loop_routing.py) ────────────
class StubAIMessage:
    """Assistant message carrying classified tool calls.

    ``tool_node`` reads ``.tool_calls`` and ``.additional_kwargs``
    (``_extraction_status`` / ``_synthetic_results``) and ``.content``;
    ``.type == "ai"`` keeps it out of the tool-message scanners.
    """

    def __init__(self, content="", tool_calls=None, extraction_status=None, synthetic=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        self.additional_kwargs = {
            "_extraction_status": extraction_status or {},
            "_synthetic_results": synthetic or {},
        }


class StubToolMessage:
    """Tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


class FakeToolNode:
    """In-memory replacement for the real ToolNode (no network I/O).

    Returns one synthetic ToolMessage per pending call so ``tool_node`` can
    finalize without dispatching a real ``declare_trade`` HTTP call.
    """

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = getattr(msgs[0], "tool_calls", None) or [] if msgs else []
        return {
            "messages": [
                StubToolMessage(content="ok", name=c.get("name")) for c in calls
            ]
        }


# ── Message builders ──────────────────────────────────────────────────────────
def _usable_consensus():
    """A usable consensus result — this single tool latches ``market_data_seen``."""
    return StubToolMessage(
        content=json.dumps(
            {"trend_score": 20, "current_price": 24250.0, "atr_14": 60.0, "rsi_14": 55.0}
        ),
        name="get_consensus_report",
    )


def _failing_core(name):
    """A CORE tool that is still FAILING (hard error → not usable, not resolved)."""
    return StubToolMessage(
        content=json.dumps({"error": f"{name}: upstream candle store fault"}),
        name=name,
    )


def _declare_hold(conviction, call_id="declare_hb"):
    """A model-declared stand-aside HOLD (conviction > 0 — NOT the conviction-0
    force_terminal path)."""
    args = {
        "action": "HOLD",
        "conviction_score": conviction,
        "setup_validation": "Tier: stand_aside. Chop, no edge — standing aside for now.",
        "execution_plan": "HOLD — no trade taken; reassess on the next pulse.",
    }
    return StubAIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "declare_trade", "args": args}],
        extraction_status={call_id: "ok"},
    )


def _heartbeat_state(messages):
    """A loop state on a HEARTBEAT resume with market_data_seen already latched
    (by consensus) and NO bounded-hunt cap reached (fresh watch/heartbeat
    counters, so ``opportunity.termination_reason`` is ``None``)."""
    return {
        "messages": messages,
        "decision": None,
        "reasoning_turns": 0,
        "market_data_seen": True,               # latched True by the consensus read alone
        "last_resume_kind": opportunity.RESUME_HEARTBEAT,
        # No cap has fired: fresh counters.
        "watch_cycles": 0,
        "heartbeat_count": 2,                    # ~2nd heartbeat wake
        "session_turns": 2,
        "session_started_at": None,
    }


# ── Sanity: the bug window is genuinely set up ────────────────────────────────
def test_bug_window_preconditions_hold():
    """The synthesized state is exactly the R6 bug window: consensus latched
    ``market_data_seen`` True while every CORE tool is still unresolved, and no
    cap has fired. (Not the defect itself — just proves the fixture is honest.)"""
    core_msgs = [_failing_core(n) for n in CORE_TOOLS]
    messages = [_usable_consensus(), *core_msgs]

    # A single usable tool latches the coarse gate...
    assert graph._market_data_seen(messages) is True
    # ...even though every CORE tool only produced a hard error (unresolved).
    for n in CORE_TOOLS:
        assert graph._market_data_seen([_failing_core(n)]) is False
    # No bounded-hunt cap has fired in this state.
    state = _heartbeat_state(messages)
    assert opportunity.termination_reason(state, graph._OPPORTUNITY_CFG, None) is None
    assert state["last_resume_kind"] == opportunity.RESUME_HEARTBEAT


# ── Documented concrete counterexample (the real premature stand-aside) ────────
def test_concrete_second_heartbeat_commits_premature_standaside():
    """Concrete counterexample: on the ~2nd heartbeat wake a conviction-74
    ``stand_aside`` HOLD is committed while four core tools (regime,
    relative-strength, session, order-flow) are still unresolved and no cap has
    fired.

    EXPECTED FAIL on unfixed code: ``tool_node`` takes the normal finalize path
    and sets ``update["decision"]`` to the HOLD — the heartbeat + unresolved-core
    condition is never consulted.
    """
    messages = [
        _usable_consensus(),
        *[_failing_core(n) for n in CORE_TOOLS],
        _declare_hold(conviction=74),
    ]
    state = _heartbeat_state(messages)

    with mock.patch.object(graph, "_base_tool_node", FakeToolNode()):
        update = tool_node(state)

    decision = update.get("decision")
    # CORRECT (fixed) behavior: the premature HOLD is NOT committed — the loop
    # keeps acquiring the core tools. This assertion FAILS on the unfixed
    # routing, confirming the premature stand-aside defect.
    committed = decision or {}
    assert decision is None, (
        "counterexample: heartbeat wake committed a premature stand-aside — "
        f"action={committed.get('action')!r}, "
        f"conviction={committed.get('conviction_score')!r} — while the "
        "core tools (regime, relative-strength, session, order-flow) were still "
        "unresolved and no bounded-hunt cap had fired"
    )


# ── Property 14 (bug condition): heartbeat + HOLD + unresolved core + no cap ──
# Feature: deep-quant-runtime-hardening, Property 14: Heartbeat gate blocks
# premature stand-aside
@settings(max_examples=120, deadline=None)
@given(
    conviction=st.integers(min_value=1, max_value=100),   # model-declared HOLD (conviction > 0)
    # At least one core tool must remain unresolved (failing); the rest are also
    # failing. We vary WHICH subset is failing so the property holds broadly.
    failing=st.lists(st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True),
)
def test_heartbeat_hold_with_unresolved_core_is_not_committed(conviction, failing):
    """For any model-declared HOLD (conviction > 0) resolved on a heartbeat wake
    while at least one core tool is still failing and no cap has fired, the
    terminal stand-aside must NOT be committed (the loop continues).

    EXPECTED FAIL on unfixed code: the ``market_data_seen`` latch (set True by
    the consensus read) lets the HOLD finalize regardless of the heartbeat or the
    unresolved core tools.
    """
    core_msgs = [_failing_core(n) for n in failing]
    messages = [_usable_consensus(), *core_msgs, _declare_hold(conviction)]
    state = _heartbeat_state(messages)

    with mock.patch.object(graph, "_base_tool_node", FakeToolNode()):
        update = tool_node(state)

    assert update.get("decision") is None, (
        f"premature stand-aside committed on a heartbeat wake (conviction="
        f"{conviction}) with unresolved core tools {sorted(failing)} and no cap"
    )
