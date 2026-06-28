"""Unit tests for the options-positioning prompt content (task 8.2).

Feature: options-agent-integration

These example-based unit tests (no live LLM, no live Rust server, no Hypothesis)
assert that the agent system prompts in ``graph.py`` were wired up with the
options-positioning guidance required by Requirement 5. They read the prompt
strings directly off the module:

  * ``graph.DEEP_QUANT_SYSTEM_PROMPT`` — the FIND-mode analyst prompt, and
  * ``graph.RISK_MANAGER_PROMPT`` — the VERIFY-mode co-pilot prompt.

Requirement 5.1:
    THE FIND-mode prompt SHALL instruct the agent to call
    `get_options_analytics` as a step in its order of operations.

Requirement 5.2:
    THE prompt SHALL instruct the agent to check options alignment before
    committing a directional trade (the "AM I FIGHTING OPTIONS POSITIONING"
    self-verification check).

Requirement 5.3:
    WHEN the proposed direction is misaligned with options positioning THE
    prompt SHALL instruct the agent to lower conviction, wait, or HOLD.

Requirement 5.4:
    THE prompt SHALL instruct the agent to respect the OI-wall
    support/resistance and the max-pain pinning when placing entry / stop /
    target.

Requirement 5.5:
    THE prompt SHALL instruct the agent to disclose the options positioning in
    its ``setup_validation`` defensibility record.

Requirement 5.6:
    WHEN options context is unavailable THE prompt SHALL instruct the agent to
    note it as unavailable and proceed (never fabricate, never abort).
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402


# ── Requirement 5.1: order-of-operations call ────────────────────────────────
def test_find_prompt_mentions_get_options_analytics():
    """Validates: Requirements 5.1

    The FIND-mode prompt must name the `get_options_analytics` tool and place it
    as an explicit OPTIONS POSITIONING step in the order of operations.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert "get_options_analytics" in prompt
    assert "2e. OPTIONS POSITIONING:" in prompt


# ── Requirement 5.2: alignment-check-before-directional self-verification ─────
def test_find_prompt_has_options_self_verification_check():
    """Validates: Requirements 5.2

    Before a directional trade the prompt must force the agent to ask whether it
    is fighting options positioning (the alignment check).
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert "AM I FIGHTING OPTIONS POSITIONING?" in prompt
    assert "check the `alignment` from `get_options_analytics`" in prompt


# ── Requirement 5.3: misaligned → lower conviction / wait / HOLD ─────────────
def test_find_prompt_misaligned_lowers_conviction_or_holds():
    """Validates: Requirements 5.3

    When options positioning is misaligned, the prompt must require exactly one
    of: lower conviction, wait for a better setup, or HOLD.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "(for example a BUY into a strong call OI-wall just overhead, or a trade "
        "against a bearish options bias), you MUST take exactly one of these "
        "actions: lower your conviction_score, wait for a better setup "
        "(e.g. via `watch_price_condition`), or HOLD."
    ) in prompt


# ── Requirement 5.4: OI-wall / max-pain placement guidance ───────────────────
def test_find_prompt_oi_wall_and_max_pain_placement_guidance():
    """Validates: Requirements 5.4

    The prompt must instruct the agent to respect OI-wall support/resistance and
    max-pain pinning when placing entry / stop / target.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "respect the OI-wall support/resistance and the max-pain pinning when "
        "placing your entry, stop, and target"
    ) in prompt
    assert (
        "do NOT set a target beyond a heavy call OI-wall just overhead, and do "
        "NOT place an entry that fights max-pain pinning"
    ) in prompt


# ── Requirement 5.5: setup_validation disclosure line ────────────────────────
def test_find_prompt_setup_validation_disclosure_line():
    """Validates: Requirements 5.5

    The defensibility record must carry an explicit "OPTIONS POSITIONING:"
    disclosure line covering PCR, max-pain, OI bias, OI walls, and alignment.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "- OPTIONS POSITIONING: State the PCR, the max-pain level, the aggregate "
        "OI bias, the nearest OI walls (support/resistance), and the Alignment "
        "taken from the `get_options_analytics` result"
    ) in prompt


# ── Requirement 5.6: unavailable-and-proceed (FIND mode) ─────────────────────
def test_find_prompt_unavailable_and_proceed():
    """Validates: Requirements 5.6

    When options context is unavailable the FIND prompt must instruct the agent
    to note it as unavailable and proceed — never fabricating, never aborting.
    """
    prompt = graph.DEEP_QUANT_SYSTEM_PROMPT
    assert (
        "If options context is unavailable (outside market hours / no snapshot / "
        "unsubscribed underlying / unavailable marker), treat it as a missing "
        "optional input"
    ) in prompt
    assert "do NOT fabricate an options bias and do NOT abort the decision" in prompt


# ── Requirement 5.1: VERIFY-mode prompt names the tool ───────────────────────
def test_verify_prompt_mentions_get_options_analytics():
    """Validates: Requirements 5.1

    The VERIFY-mode (risk-manager) prompt must also consult
    `get_options_analytics` while verifying a user-proposed trade.
    """
    prompt = graph.RISK_MANAGER_PROMPT
    assert "get_options_analytics" in prompt


# ── Requirement 5.2: VERIFY-mode misalignment warning ────────────────────────
def test_verify_prompt_misalignment_warning():
    """Validates: Requirements 5.2

    When a user-proposed directional trade is misaligned with options
    positioning, the VERIFY prompt must require an explicit warning statement.
    """
    prompt = graph.RISK_MANAGER_PROMPT
    assert (
        "you MUST include an explicit warning statement in your verification "
        "output that the proposed trade fights the prevailing options positioning"
    ) in prompt


# ── Requirement 5.6: unavailable-and-proceed (VERIFY mode) ───────────────────
def test_verify_prompt_unavailable_and_proceed():
    """Validates: Requirements 5.6

    When options context is unavailable the VERIFY prompt must instruct the agent
    to note it as unavailable and proceed with verification, not block the trade.
    """
    prompt = graph.RISK_MANAGER_PROMPT
    assert (
        "If options context is unavailable, note it as unavailable and proceed "
        "with verification"
    ) in prompt
    assert (
        "do NOT block the trade solely because options positioning could not be "
        "computed"
    ) in prompt
