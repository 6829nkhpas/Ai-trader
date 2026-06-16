"""Property-based test for missing-stance handling (debate.py, task 3.6).

Feature: multi-agent-debate

This module implements design **Property 30: A missing stance does not block or
fabricate a verdict**:

    When at least one stance is unavailable (``available == False``, treated as
    effective strength 0), ``classify_consensus``, ``derive_conviction``, and
    ``judge_directional_bias`` still return well-formed results (a valid
    consensus enum, a conviction in ``[0, 100]``, a valid bias in
    ``{long, short, hold}``) without raising and without fabricating the missing
    stance's strength. The available stance drives the verdict; the missing
    stance contributes effective strength 0 -- so its nominal ``strength`` field
    is ignored entirely.

Validates: Requirements 12.2.

The strategy generates pairs where AT LEAST ONE stance has ``available == False``
(and arbitrary other fields, including arbitrary, far-out-of-range nominal
strength values that must be ignored). For every pair the test asserts:

  * ``classify_consensus`` returns a member of ``DEBATE_CONSENSUS_VALUES``;
  * ``derive_conviction`` returns an ``int`` in ``[0, 100]``;
  * ``judge_directional_bias`` returns a member of ``{"long", "short", "hold"}``;
  * the unavailable stance's nominal ``strength`` does not affect any outcome --
    re-running the same pair with the unavailable stance's ``strength`` field
    mutated to a different value yields *identical* results, proving the missing
    stance's strength is ignored (it is treated as 0), not fabricated.

The sys.path / import pattern mirrors the sibling ``test_debate_*`` modules.
"""

import os
import sys
from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (debate.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from debate import (  # noqa: E402
    DEBATE_CONSENSUS_VALUES,
    DEBATE_LEANS,
    STRENGTH_MAX,
    STRENGTH_MIN,
    DebateStance,
    classify_consensus,
    derive_conviction,
    judge_directional_bias,
)

_DIRECTIONAL_BIASES = ("long", "short", "hold")


@st.composite
def _stance(draw, available):
    """An arbitrary ``DebateStance`` with a caller-fixed ``available`` flag.

    Strength spans below ``STRENGTH_MIN``, the in-range band, and above
    ``STRENGTH_MAX`` so both the clamping path (for available stances) and the
    ignored-strength path (for unavailable stances) are exercised.
    """
    return DebateStance(
        role=draw(st.sampled_from(["bull", "bear", "", "judge"])),
        lean=draw(st.sampled_from(list(DEBATE_LEANS) + ["garbled"])),
        strength=draw(st.integers(min_value=STRENGTH_MIN - 50, max_value=STRENGTH_MAX + 50)),
        arguments=draw(st.lists(st.text(max_size=20), max_size=5)),
        biggest_risk=draw(st.text(max_size=30)),
        available=available,
    )


@st.composite
def _pair_with_missing_stance(draw):
    """A (bull, bear) pair in which AT LEAST ONE stance is unavailable.

    ``which`` selects whether the bull, the bear, or both are missing; the
    remaining stance (if any) is an arbitrary available stance that should drive
    the verdict.
    """
    which = draw(st.sampled_from(["bull_missing", "bear_missing", "both_missing"]))
    if which == "bull_missing":
        return draw(_stance(available=False)), draw(_stance(available=True))
    if which == "bear_missing":
        return draw(_stance(available=True)), draw(_stance(available=False))
    return draw(_stance(available=False)), draw(_stance(available=False))


# ─────────────────────────────────────────────────────────────────────────────
# Property 30: A missing stance does not block or fabricate a verdict
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 30: A missing stance does not block or fabricate a verdict
@settings(max_examples=100, deadline=None)
@given(pair=_pair_with_missing_stance(), mutated_strength=st.integers(min_value=STRENGTH_MIN - 50, max_value=STRENGTH_MAX + 50))
def test_property_30_missing_stance_does_not_block_or_fabricate(pair, mutated_strength):
    """Validates: Requirements 12.2

    With at least one unavailable stance, all three verdict functions return
    well-formed results without raising, and the unavailable stance's nominal
    strength is ignored (mutating it leaves every outcome identical).
    """
    bull, bear = pair

    # ── Well-formed, never-raising results (a missing stance does not block). ──
    consensus = classify_consensus(bull, bear)
    assert consensus in DEBATE_CONSENSUS_VALUES, (
        f"classify_consensus returned {consensus!r}, not in {DEBATE_CONSENSUS_VALUES}"
    )

    conviction = derive_conviction(bull, bear, consensus)
    assert isinstance(conviction, int) and not isinstance(conviction, bool), (
        f"derive_conviction must return an int, got {type(conviction).__name__}"
    )
    assert 0 <= conviction <= 100, f"conviction {conviction} out of [0, 100]"

    bias = judge_directional_bias(bull, bear, consensus)
    assert bias in _DIRECTIONAL_BIASES, (
        f"judge_directional_bias returned {bias!r}, not in {_DIRECTIONAL_BIASES}"
    )

    # ── The missing stance's nominal strength is ignored (not fabricated). ─────
    # Mutate the unavailable stance(s)' strength to an arbitrary other value and
    # confirm every outcome is byte-for-byte identical: the verdict is driven by
    # the available stance, with the missing one contributing effective 0.
    bull_m = replace(bull, strength=mutated_strength) if not bull.available else bull
    bear_m = replace(bear, strength=mutated_strength) if not bear.available else bear

    consensus_m = classify_consensus(bull_m, bear_m)
    conviction_m = derive_conviction(bull_m, bear_m, consensus_m)
    bias_m = judge_directional_bias(bull_m, bear_m, consensus_m)

    assert consensus_m == consensus, (
        "consensus changed when an unavailable stance's strength was mutated: "
        f"{consensus!r} -> {consensus_m!r} (missing strength must be ignored)"
    )
    assert conviction_m == conviction, (
        "conviction changed when an unavailable stance's strength was mutated: "
        f"{conviction} -> {conviction_m} (missing strength must be ignored)"
    )
    assert bias_m == bias, (
        "directional bias changed when an unavailable stance's strength was "
        f"mutated: {bias!r} -> {bias_m!r} (missing strength must be ignored)"
    )
