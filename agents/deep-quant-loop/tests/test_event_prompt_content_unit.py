"""Unit tests for the earnings/event-date-risk prompt content (task 10.2).

Feature: earnings-event-risk-gate

These example-based unit tests (no live LLM, no live Rust server, no Hypothesis)
assert that the agent system prompts in ``graph.py`` were wired up with the
event-date-risk-gate guidance required by Requirement 7. They read the prompt
strings directly off the module:

  * ``graph.DEEP_QUANT_SYSTEM_PROMPT`` — the FIND-mode analyst prompt, and
  * ``graph.RISK_MANAGER_PROMPT`` — the VERIFY-mode co-pilot prompt.

Requirement 7.1:
    THE FIND-mode order_of_operations SHALL instruct the agent to call
    `get_event_risk` for the symbol under analysis, passing the intended
    Holding_Horizon of the setup being considered.

Requirement 7.2:
    THE FIND-mode self_verification_protocol SHALL instruct the agent to check
    the Event_Risk before committing a directional (BUY/SELL) trade.

Requirement 7.3:
    WHEN the Event_Risk is `through_event` THE prompt SHALL instruct the agent
    to take exactly one tightening action (shorten horizon / reduce size /
    stand aside HOLD) and SHALL NOT permit loosening any criterion.

Requirement 7.4:
    WHEN the Event_Risk is `imminent` THE prompt SHALL instruct the agent to
    reduce conviction or size and to state the event proximity.

Requirement 7.5:
    THE setup_validation_disclosure SHALL instruct the agent to state the
    Event_Risk, the days-until-event, and the Event_Recommendation.

Requirement 7.6:
    THE VERIFY-mode RISK_MANAGER_PROMPT SHALL instruct the agent to consult
    `get_event_risk` while verifying and to include an explicit warning on a
    `through_event` risk.

Requirement 7.7:
    WHEN the event risk is unavailable THE FIND and VERIFY prompts SHALL
    instruct the agent to note it as unavailable and proceed (never fabricate,
    never block).

Assertions favor robust key-phrase substring checks (and case-insensitive
keyword checks where sensible) over brittle exact long-string matches.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402


# ── Requirement 7.1: order-of-operations call with intended Holding_Horizon ──
def test_find_prompt_calls_get_event_risk_with_holding_horizon():
    """Validates: Requirements 7.1

    The FIND-mode prompt must name the `get_event_risk` tool, place it as an
    explicit EVENT-DATE RISK GATE step in the order of operations, and instruct
    the agent to pass the intended Holding_Horizon of the setup.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert "get_event_risk" in prompt
    assert "2f. EVENT-DATE RISK GATE:" in prompt
    # Calls for the symbol under analysis, passing the intended Holding_Horizon.
    assert (
        "Call `get_event_risk` with the symbol under analysis, passing the "
        "intended Holding_Horizon"
    ) in prompt
    # The two documented horizon values are described.
    lowered = prompt.lower()
    assert "intraday" in lowered
    assert "multi_session" in lowered


# ── Requirement 7.2: check Event_Risk before a directional trade ─────────────
def test_find_prompt_has_event_self_verification_check():
    """Validates: Requirements 7.2

    Before a directional (BUY/SELL) trade the prompt must force the agent to
    check the Event_Risk from `get_event_risk`.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert "WOULD THIS TRADE BE HELD THROUGH A SCHEDULED EVENT?" in prompt
    assert "check the `event_risk` from `get_event_risk`" in prompt
    # The check is scoped to directional (BUY/SELL) trades, excluding HOLD.
    assert (
        "Before committing a DIRECTIONAL trade (a BUY or SELL decision"
        in prompt
    )


# ── Requirement 7.3: through_event → exactly one tightening action, no loosen ─
def test_find_prompt_through_event_one_tightening_action_no_loosening():
    """Validates: Requirements 7.3

    On a `through_event` risk the prompt must require exactly one tightening
    action (shorten horizon so the trade closes before the event / reduce size /
    stand aside HOLD) and must forbid loosening any criterion.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "If the event_risk is `through_event`, you MUST take EXACTLY ONE of "
        "these tightening actions"
    ) in prompt
    # The three permitted tightening options.
    assert "shorten the holding horizon so the trade closes BEFORE the event" in prompt
    assert "reduce your position size" in prompt
    assert "stand aside (HOLD)" in prompt
    # Must NOT loosen any criterion.
    assert "must NOT loosen any criterion on the basis of the event context" in prompt


# ── Requirement 7.4: imminent → reduce conviction/size + state proximity ─────
def test_find_prompt_imminent_reduce_and_state_proximity():
    """Validates: Requirements 7.4

    On an `imminent` risk the prompt must require reducing conviction or size
    and stating the event proximity (the days-until-event).
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "If the event_risk is `imminent`, you MUST reduce your conviction_score "
        "or size and state the event proximity"
    ) in prompt
    assert "days-until-event" in prompt


# ── Requirement 7.5: setup_validation disclosure line ────────────────────────
def test_find_prompt_setup_validation_disclosure_line():
    """Validates: Requirements 7.5

    The defensibility record must carry an explicit "EVENT RISK:" disclosure
    line covering the Event_Risk, the days-until-event, and the
    Event_Recommendation.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "- EVENT RISK: State the Event_Risk, the days-until-event, and the "
        "Event_Recommendation taken from the `get_event_risk` result"
    ) in prompt


# ── Requirement 7.7: unavailable-and-proceed (FIND mode) ─────────────────────
def test_find_prompt_unavailable_and_proceed():
    """Validates: Requirements 7.7

    When the event risk is unavailable the FIND prompt must instruct the agent
    to note it as unavailable and proceed — never fabricating a date, never
    aborting the decision.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "If the event risk is unavailable (no event source configured / no "
        "upcoming event known for the symbol / source unreachable / unavailable "
        "marker), treat it as a missing optional input"
    ) in prompt
    assert "note it as unavailable and proceed" in prompt
    assert "do NOT fabricate an event date and do NOT abort the decision" in prompt


# ── Requirement 7.6: VERIFY-mode consult + through_event warning ─────────────
def test_verify_prompt_consults_get_event_risk_with_horizon():
    """Validates: Requirements 7.6

    The VERIFY-mode (risk-manager) prompt must also consult `get_event_risk`
    while verifying a user-proposed trade, passing the intended Holding_Horizon.
    """
    prompt = graph.RISK_MANAGER_PROMPT
    assert "get_event_risk" in prompt
    assert (
        "Consult `get_event_risk` for the symbol while verifying, passing the "
        "intended Holding_Horizon"
    ) in prompt


def test_verify_prompt_through_event_warning_statement():
    """Validates: Requirements 7.6

    When a user-proposed directional trade carries a `through_event` risk, the
    VERIFY prompt must require an explicit warning statement that the trade would
    be held through a scheduled event and is exposed to overnight gap risk.
    """
    prompt = graph.RISK_MANAGER_PROMPT
    assert "`through_event`" in prompt
    assert (
        "you MUST include an explicit WARNING statement in your verification "
        "output that the proposed trade would be held through a scheduled event"
    ) in prompt
    assert "overnight gap risk" in prompt


# ── Requirement 7.7: unavailable-and-proceed (VERIFY mode) ───────────────────
def test_verify_prompt_unavailable_and_proceed():
    """Validates: Requirements 7.7

    When the event risk is unavailable the VERIFY prompt must instruct the agent
    to note it as unavailable and proceed with verification, not block the trade.
    """
    prompt = graph.RISK_MANAGER_PROMPT
    assert (
        "If the event risk is unavailable, note it as unavailable and proceed "
        "with verification"
    ) in prompt
    assert (
        "do NOT block the trade solely because the event risk could not be "
        "computed"
    ) in prompt
