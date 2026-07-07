"""Example/integration test for the VERIFY-mode Bear devil's advocate (graph.py).

# Feature: multi-agent-debate (task 15.5): VERIFY devil's advocate

This is a concrete EXAMPLE test (not a property test) of the two integration
points that make the VERIFY devil's advocate real:

  * ``_should_run_verify_devils_advocate(state, mode, messages)`` — gates the run
    so the Bear_Agent devil's advocate fires on a VERIFY run (once, after the
    Shared_Evidence is available) and NEVER on a FIND / DEBATE / QA run.
  * ``run_verify_devils_advocate(state, messages)`` — invokes the read-only-bound
    Bear LLM against the user-proposed trade and returns an ``AIMessage`` that
    SURFACES the devil's-advocate stance in the verification reasoning (R11.1,
    R11.2), tagged ``additional_kwargs["role"] == "bear"``, with NO tool calls,
    and WITHOUT ever setting ``state["decision"]`` (R11.3).

It also exercises ``call_model`` end-to-end (with BOTH the Bear role LLM AND the
risk-manager ``llm_with_tools`` stubbed, so no network I/O happens) to confirm
that on a VERIFY run the devil's-advocate message is prepended BEFORE the
verdict response and the one-shot latch ``verify_devils_advocate_done`` is set.

The sys.path / import pattern mirrors the sibling
``test_debate_verify_devils_advocate_never_commits_properties.py``. Importing
``graph`` constructs LLM client objects at import time but performs no network
I/O, and the stubbed ``get_role_llm`` / ``llm_with_tools`` ensure no real
LLM/network call happens during the test.

Validates: Requirements 11.1, 11.2.
"""

import json
import os
import sys

from langchain_core.messages import AIMessage, ToolMessage

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    call_model,
    run_verify_devils_advocate,
    _should_run_verify_devils_advocate,
)


# A fixed, clearly-bearish stance the stub Bear LLM emits. It uses recognizable
# stance fields so ``debate.parse_stance`` marks it available and round-trips it
# into the surfaced message.
_BEARISH_STANCE = {
    "lean": "short",
    "strength": 82,
    "arguments": [
        "Entry sits right under prior supply — poor location for a long.",
        "Stop is too tight against ATR; likely shaken out before the move.",
        "Target implies a sub-1R reward against the macro downtrend.",
    ],
    "biggest_risk": "A surprise momentum breakout could still carry the long.",
}


class _StubBearResponse:
    """Stand-in for the AIMessage returned by the Bear role LLM.

    ``run_verify_devils_advocate`` reads only ``response.content`` (via
    ``getattr(response, "content", "")``); we expose ``content`` plus an empty
    ``tool_calls`` for safety.
    """

    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.additional_kwargs = {}


class _StubBearLLM:
    """Stub returned by the monkeypatched ``graph.get_role_llm``.

    ``.invoke(messages)`` ignores the messages and returns the fixed bearish
    stance JSON, so NO real LLM / network call ever happens.
    """

    def __init__(self, stance):
        self._content = json.dumps(stance)
        self.invoked_with = []

    def invoke(self, messages):
        self.invoked_with.append(messages)
        return _StubBearResponse(self._content)


class _StubVerdictLLM:
    """Stub for the risk-manager ``llm_with_tools`` used by ``call_model``.

    ``.invoke(messages)`` returns a plain verdict ``AIMessage`` with NO tool
    calls and content that contains no custom-token markup, so ``call_model``
    treats it as a reasoning-only verdict turn (no network I/O).
    """

    def __init__(self):
        self.invoked_with = []

    def invoke(self, messages):
        self.invoked_with.append(messages)
        return AIMessage(
            content="VERDICT: Having weighed the devil's advocate, I HOLD.",
            tool_calls=[],
        )


def _build_verify_state():
    """A minimal VERIFY state: a BUY manual trade, evidence seen, latch unset."""
    messages = [
        AIMessage(content="verification research complete", tool_calls=[]),
        ToolMessage(
            content="trend=down, price below 50/200 EMA",
            name="get_multi_tf_trend",
            tool_call_id="ev_0",
        ),
        ToolMessage(
            content="supply zone overhead at 101-102",
            name="get_support_resistance",
            tool_call_id="ev_1",
        ),
    ]
    return {
        "messages": messages,
        "mode": "VERIFY",
        "symbol": "AAPL",
        "timeframe": "10m",
        "market_data_seen": True,
        "manual_trade": {
            "side": "BUY",
            "entry": 100.0,
            "stop_loss": 98.0,
            "take_profit": 105.0,
            "user_analysis": "I think this breaks out.",
        },
        # latch intentionally unset
    }


# ─────────────────────────────────────────────────────────────────────────────
# _should_run_verify_devils_advocate: True only on VERIFY, False elsewhere.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate (task 15.5): VERIFY devil's advocate
def test_should_run_devils_advocate_true_for_verify():
    """Validates: Requirements 11.1

    A VERIFY run with the latch unset and the Shared_Evidence available triggers
    the Bear devil's advocate.
    """
    state = _build_verify_state()
    assert (
        _should_run_verify_devils_advocate(state, "VERIFY", state["messages"]) is True
    )


# Feature: multi-agent-debate (task 15.5): VERIFY devil's advocate
def test_should_not_run_devils_advocate_for_non_verify_modes():
    """Validates: Requirements 11.1

    FIND / DEBATE / QA runs leave the devil's advocate inert — it never fires
    outside VERIFY.
    """
    state = _build_verify_state()
    for mode in ("FIND", "DEBATE", "QA"):
        assert (
            _should_run_verify_devils_advocate(state, mode, state["messages"])
            is False
        ), f"devil's advocate must not run for mode {mode!r}"


# Feature: multi-agent-debate (task 15.5): VERIFY devil's advocate
def test_should_not_run_devils_advocate_once_latched():
    """Validates: Requirements 11.1

    Once it has run (``verify_devils_advocate_done`` latched), it does not run
    again within the same VERIFY run.
    """
    state = _build_verify_state()
    state["verify_devils_advocate_done"] = True
    assert (
        _should_run_verify_devils_advocate(state, "VERIFY", state["messages"])
        is False
    )


# ─────────────────────────────────────────────────────────────────────────────
# run_verify_devils_advocate: surfaces the Bear stance, tagged, no tool calls,
# never decides.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate (task 15.5): VERIFY devil's advocate
def test_run_devils_advocate_surfaces_bear_stance():
    """Validates: Requirements 11.1, 11.2

    With the Bear role LLM stubbed to return a fixed bearish stance, the function
    returns an ``AIMessage`` that:
      * surfaces the DEVIL'S ADVOCATE stance (label + the stance JSON) in the
        verification reasoning,
      * is tagged ``additional_kwargs["role"] == "bear"``,
      * carries NO tool calls (it cannot commit or block), and
      * does NOT set ``state["decision"]`` (the verdict path stays authority).
    """
    stub = _StubBearLLM(_BEARISH_STANCE)
    original_get_role_llm = graph.get_role_llm
    graph.get_role_llm = lambda role: stub
    try:
        state = _build_verify_state()
        result = run_verify_devils_advocate(state, state["messages"])

        # It returns a surfaced stance message.
        assert isinstance(result, AIMessage)

        # The stance is surfaced in the verification reasoning: the explicit
        # DEVIL'S ADVOCATE label plus the parsed bearish stance values.
        content = result.content or ""
        assert "DEVIL'S ADVOCATE" in content
        assert '"lean": "short"' in content
        assert '"strength": 82' in content
        # The arguments survive into the surfaced stance JSON.
        assert "supply" in content.lower()

        # It is tagged as the Bear role for downstream role-tagged reasoning.
        assert result.additional_kwargs.get("role") == "bear"

        # It carries NO tool calls — it cannot execute / commit / block.
        assert not (getattr(result, "tool_calls", None) or [])

        # It NEVER itself decides — the verdict path is the sole authority.
        assert "decision" not in state

        # The Bear LLM was actually invoked exactly once over the evidence.
        assert len(stub.invoked_with) == 1
    finally:
        graph.get_role_llm = original_get_role_llm


# ─────────────────────────────────────────────────────────────────────────────
# call_model end-to-end (both LLMs stubbed): the devil's-advocate message is
# prepended BEFORE the verdict response and the one-shot latch is set.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate (task 15.5): VERIFY devil's advocate
def test_call_model_prepends_devils_advocate_before_verdict():
    """Validates: Requirements 11.1, 11.2

    On a VERIFY run, ``call_model`` runs the devil's advocate ONCE and the
    returned ``update["messages"]`` is ``[devils_advocate_msg, verdict_response]``
    — the Bear stance is surfaced BEFORE the verdict — and
    ``update["verify_devils_advocate_done"]`` is True.
    """
    bear_stub = _StubBearLLM(_BEARISH_STANCE)
    verdict_stub = _StubVerdictLLM()

    original_get_role_llm = graph.get_role_llm
    original_llm_with_tools = graph.llm_with_tools
    # call_model selects the verdict binding via `_llm_for_profile(state)`, which
    # returns `non_fno_llm_with_tools` for any non-F&O (here profile-less ->
    # INTRADAY) state. Stub BOTH bindings so the profile selector cannot reach a
    # real model regardless of which handle it picks (no network I/O).
    original_non_fno_llm_with_tools = graph.non_fno_llm_with_tools
    graph.get_role_llm = lambda role: bear_stub
    graph.llm_with_tools = verdict_stub
    graph.non_fno_llm_with_tools = verdict_stub
    try:
        state = _build_verify_state()
        update = call_model(state)

        # The one-shot latch is set so the devil's advocate runs exactly once.
        assert update.get("verify_devils_advocate_done") is True

        # Both LLMs were used: the Bear devil's advocate AND the verdict path.
        assert len(bear_stub.invoked_with) == 1
        assert len(verdict_stub.invoked_with) == 1

        # The update carries the devil's-advocate message BEFORE the verdict.
        msgs = update["messages"]
        assert len(msgs) == 2
        devils_msg, verdict_msg = msgs

        assert isinstance(devils_msg, AIMessage)
        assert devils_msg.additional_kwargs.get("role") == "bear"
        assert "DEVIL'S ADVOCATE" in (devils_msg.content or "")
        assert not (getattr(devils_msg, "tool_calls", None) or [])

        # The verdict response is the stubbed risk-manager output and comes last.
        assert "VERDICT" in (verdict_msg.content or "")
    finally:
        graph.get_role_llm = original_get_role_llm
        graph.llm_with_tools = original_llm_with_tools
        graph.non_fno_llm_with_tools = original_non_fno_llm_with_tools
