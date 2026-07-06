"""Property-based test that the Trade_Validator stays authoritative on the
Judge's trade (graph.py + validator.py, task 8.2).

Feature: multi-agent-debate

This module implements design **Property 11: The Trade_Validator stays
authoritative on the Judge's trade**:

    For ANY Judge ``declare_trade``, a declaration the Trade_Validator rejects
    never finalizes a decision, and any finalized directional (BUY/SELL) decision
    satisfies ``validate_trade`` for the same inputs — the debate neither relaxes
    nor bypasses any hard risk rule.

Validates: Requirements 4.6, 5.2.

The Judge node (``graph.judge_node``) commits through the UNCHANGED single-agent
chokepoint: a ``declare_trade`` call goes through the real validator (mirrored by
``validator.validate_trade``); the tool returns a ``TRADE_REJECTED: ...`` marker
on rejection, and ``graph._declare_was_rejected`` detects that marker so the run
does NOT finalize on a rejected declaration. The exact gate in ``judge_node`` is:

    cand = _decision_from_declare(call_dicts)
    if cand is not None and _declare_was_rejected(declare_tmsgs):
        decision = None          # rejected -> NOT finalized; Judge must revise

Because the debate reuses this gate verbatim and the SAME ``validate_trade``,
three structural invariants prove the validator stays authoritative — tested here
WITHOUT a live LLM or Rust server:

1. ``_declare_was_rejected`` returns ``True`` iff some ``declare_trade`` tool
   result carries the ``TRADE_REJECTED`` marker (rejection detection is total).
2. The hard risk rules are unchanged: a directional trade passes ``validate_trade``
   IFF its Risk:Reward >= 2.0 AND (when ATR is known) its stop distance
   >= 1.5 x ATR; a HOLD always passes.
3. Tying them together: a trade the validator rejects produces a marked
   ``declare_trade`` result that the judge gate turns into ``decision = None``
   (never finalized), while a trade the validator passes finalizes a directional
   decision that itself satisfies ``validate_trade`` for the same inputs.

The sys.path / import pattern mirrors the sibling debate property tests.
Importing ``graph`` constructs LLM client objects at import time but performs no
network I/O, and nothing here invokes a real LLM / Rust server.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py / validator.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import _declare_was_rejected, _decision_from_declare  # noqa: E402
from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    validate_trade,
    MIN_RISK_REWARD,
    MIN_STOP_ATR_MULTIPLE,
)


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a LangChain ToolMessage. ``_is_tool_message`` matches type
    ``'tool'`` and ``_declare_was_rejected`` reads ``.name`` + ``.content``."""

    type = "tool"

    def __init__(self, content, name):
        self.content = content
        self.name = name


# The exact rejection marker the declare_trade tool emits when the authoritative
# Trade_Validator refuses to commit (mirrors tools.py). The graph treats any
# declare_trade result containing this token as a non-finalizing turn.
_REJECT_MARKER = "TRADE_REJECTED"


def _rejected_content(action, reason):
    """A declare_trade tool result mirroring the tool's rejection format."""
    return (
        f"{_REJECT_MARKER}: the Trade_Validator rejected this {action} because "
        f"'{reason}'. Revise the entry/stop_loss/take_profit so Risk:Reward "
        f">= 1:2 and the stop is >= 1.5x ATR, then call declare_trade again."
    )


def _committed_content(action):
    """A declare_trade tool result for an accepted (committed) trade."""
    return f"TRADE_COMMITTED: the {action} passed the Trade_Validator and was committed."


# Non-declare_trade tool names whose results must NEVER be read as a declaration
# rejection even if they happen to contain the marker token.
_OTHER_TOOL_NAMES = st.sampled_from(
    [
        "get_candles",
        "get_consensus_report",
        "get_multi_tf_trend",
        "get_market_regime",
        "get_support_resistance",
    ]
)


# ─────────────────────────────────────────────────────────────────────────────
# Invariant 1: rejection detection is total — _declare_was_rejected is True iff
# some declare_trade result carries the TRADE_REJECTED marker.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 11: The Trade_Validator stays authoritative on the Judge's trade
@settings(max_examples=100, deadline=None)
@given(
    # Each declare_trade result: (carries_reject_marker, action).
    declare_results=st.lists(
        st.tuples(st.booleans(), st.sampled_from(["BUY", "SELL", "HOLD"])),
        max_size=4,
    ),
    # Noise: arbitrary non-declare_trade tool results, some of which may even
    # contain the marker token — they must be ignored by the detector.
    other_results=st.lists(
        st.tuples(_OTHER_TOOL_NAMES, st.text(max_size=60)),
        max_size=4,
    ),
)
def test_property_11_rejection_detection_is_total(declare_results, other_results):
    """Validates: Requirements 4.6, 5.2

    ``_declare_was_rejected`` returns ``True`` exactly when at least one
    ``declare_trade`` tool result carries the ``TRADE_REJECTED`` marker. Results
    from other tools never count, even if their content embeds the token.
    """
    messages = []
    expected_rejected = False
    for carries_marker, action in declare_results:
        if carries_marker:
            content = _rejected_content(action, "risk-reward ratio below the 1:2 minimum")
            expected_rejected = True
        else:
            content = _committed_content(action)
        messages.append(StubToolMessage(content=content, name="declare_trade"))

    # Interleave non-declare tool results (ignored by the detector).
    for name, text in other_results:
        messages.append(StubToolMessage(content=text, name=name))

    assert _declare_was_rejected(messages) is expected_rejected, (
        "_declare_was_rejected must be True iff a declare_trade result carries "
        f"the {_REJECT_MARKER!r} marker (expected {expected_rejected}, "
        f"got {_declare_was_rejected(messages)})"
    )


# A non-declare_trade tool result containing the marker token must NOT be read as
# a declaration rejection (the detector is scoped to declare_trade results only).
# Feature: multi-agent-debate, Property 11: The Trade_Validator stays authoritative on the Judge's trade
@settings(max_examples=100, deadline=None)
@given(name=_OTHER_TOOL_NAMES, action=st.sampled_from(["BUY", "SELL", "HOLD"]))
def test_property_11_marker_on_other_tool_is_ignored(name, action):
    """Validates: Requirements 4.6, 5.2

    The marker only counts on a ``declare_trade`` result; the same token on any
    other tool result is ignored, so unrelated prose can never spuriously block a
    finalization.
    """
    msg = StubToolMessage(content=_rejected_content(action, "noise"), name=name)
    assert _declare_was_rejected([msg]) is False


# ─────────────────────────────────────────────────────────────────────────────
# Invariant 2 + 3: the hard risk rules are unchanged AND a rejected trade never
# finalizes while a passing directional trade does (and satisfies validate_trade).
# ─────────────────────────────────────────────────────────────────────────────

# Strategies producing a wide mix of passing and failing directional trades:
# the risk/reward/atr ranges straddle the RR>=2 and stop>=1.5*ATR boundaries.
_ACTIONS = st.sampled_from([Action.BUY, Action.SELL])
_entry = st.floats(min_value=50.0, max_value=5000.0, allow_nan=False, allow_infinity=False)
_risk = st.floats(min_value=0.1, max_value=200.0, allow_nan=False, allow_infinity=False)
_reward = st.floats(min_value=0.1, max_value=800.0, allow_nan=False, allow_infinity=False)
# ATR either unknown (None) or a positive value that straddles risk/1.5.
_atr = st.one_of(
    st.none(),
    st.floats(min_value=0.01, max_value=300.0, allow_nan=False, allow_infinity=False),
)


def _levels_for(action, entry, risk, reward):
    """Build direction-consistent ExecutionLevels for the given action."""
    if action == Action.BUY:
        # stop_loss < entry < take_profit
        return ExecutionLevels(entry=entry, stop_loss=entry - risk, take_profit=entry + reward)
    # SELL: take_profit < entry < stop_loss
    return ExecutionLevels(entry=entry, stop_loss=entry + risk, take_profit=entry - reward)


def _stop_too_tight(risk, atr):
    """The hard StopTooTight rule: only applies when ATR is known/finite/positive."""
    if atr is None:
        return False
    return risk < MIN_STOP_ATR_MULTIPLE * atr


# Feature: multi-agent-debate, Property 11: The Trade_Validator stays authoritative on the Judge's trade
@settings(max_examples=100, deadline=None)
@given(action=_ACTIONS, entry=_entry, risk=_risk, reward=_reward, atr=_atr)
def test_property_11_validator_hard_rules_unchanged(action, entry, risk, reward, atr):
    """Validates: Requirements 4.6, 5.2

    For a direction-consistent BUY/SELL, ``validate_trade`` passes IFF the hard
    risk rules hold unchanged: Risk:Reward >= 2.0 AND (when ATR is known) the stop
    distance >= 1.5 x ATR. The debate cannot relax or bypass these.
    """
    levels = _levels_for(action, entry, risk, reward)
    outcome = validate_trade(action, levels, atr)

    rr = reward / risk
    expected_pass = (rr >= MIN_RISK_REWARD) and (not _stop_too_tight(risk, atr))

    assert outcome.is_pass() is expected_pass, (
        f"validate_trade verdict must equal the hard-rule conjunction "
        f"(RR={rr:.4f} >= {MIN_RISK_REWARD}, stop_ok={not _stop_too_tight(risk, atr)}); "
        f"expected pass={expected_pass}, got pass={outcome.is_pass()}"
    )

    # Any PASS necessarily satisfies BOTH hard rules for the same inputs.
    if outcome.is_pass():
        assert rr >= MIN_RISK_REWARD
        assert not _stop_too_tight(risk, atr)


# Feature: multi-agent-debate, Property 11: The Trade_Validator stays authoritative on the Judge's trade
@settings(max_examples=100, deadline=None)
@given(action=_ACTIONS, entry=_entry, risk=_risk, reward=_reward, atr=_atr)
def test_property_11_rejected_trade_never_finalizes(action, entry, risk, reward, atr):
    """Validates: Requirements 4.6, 5.2

    Reproduces the judge_node finalization gate WITHOUT an LLM: a declare_trade
    whose validator outcome is a rejection produces a TRADE_REJECTED result that
    the gate turns into ``decision = None`` (never finalized). A passing trade
    finalizes a directional decision that itself satisfies ``validate_trade`` for
    the same inputs — so the validator stays authoritative on the Judge's trade.
    """
    levels = _levels_for(action, entry, risk, reward)
    outcome = validate_trade(action, levels, atr)
    action_str = action.value

    # The declare_trade tool call the Judge emitted (structured args -> decision).
    call_dicts = [
        {
            "name": "declare_trade",
            "args": {
                "action": action_str,
                "conviction_score": 80,
                "entry": levels.entry,
                "stop_loss": levels.stop_loss,
                "take_profit": levels.take_profit,
                "atr_14": atr,
            },
        }
    ]

    # The tool result mirrors what declare_trade returns for this validator outcome.
    if outcome.is_pass():
        declare_tmsgs = [StubToolMessage(_committed_content(action_str), name="declare_trade")]
    else:
        reason = outcome.reason.message if outcome.reason is not None else "rejected"
        declare_tmsgs = [StubToolMessage(_rejected_content(action_str, reason), name="declare_trade")]

    # ── The UNCHANGED judge_node finalization gate ───────────────────────────
    cand = _decision_from_declare(call_dicts)
    assert cand is not None  # a declare_trade call always yields a candidate
    finalized = None if _declare_was_rejected(declare_tmsgs) else cand

    if outcome.is_pass():
        # A validated directional trade finalizes ...
        assert finalized is not None, "a validator-passing trade must finalize"
        assert finalized["action"] == action_str
        # ... and the finalized decision satisfies validate_trade for the SAME inputs.
        refinalized = validate_trade(
            Action.from_str_lenient(finalized["action"]),
            ExecutionLevels(
                entry=finalized["entry"],
                stop_loss=finalized["stop_loss"],
                take_profit=finalized["take_profit"],
            ),
            finalized["atr_14"],
        )
        assert refinalized.is_pass(), (
            "a finalized directional decision must satisfy validate_trade for the "
            "same inputs (the validator stays authoritative)"
        )
    else:
        # A rejected declaration is NEVER finalized — the debate cannot bypass it.
        assert finalized is None, (
            "a declaration the Trade_Validator rejects must never finalize a decision"
        )
