# Feature: deep-quant-runtime-hardening, Property 5: the prose level extractor
# rejects volatility multipliers ("1.5x ATR") and ATR-token numbers as prices.
"""R4 verification property test — level extractor rejects multipliers.

Feature: deep-quant-runtime-hardening

Property 5 (Expected Behavior), Python ``graph._parse_levels_from_text`` seam:

    When a number in the prose is immediately followed (optionally after further
    digits/dots or whitespace) by an ``x`` / ``X`` / ``×`` multiplier token or an
    ``ATR`` token, that number is a volatility *multiplier*, not a defensible
    price. The extractor must NEVER capture it as ``entry`` / ``stop_loss`` /
    ``take_profit`` — the field is omitted rather than filled with the spurious
    multiplier value.

    Validates: Requirements 4.2.

This is a dedicated VERIFICATION test: it encodes the CORRECT (fixed) behavior
and must PASS against the guarded regexes (``_NOT_MULT`` on ``_SL_RE`` /
``_TP_RE`` / ``_ENTRY_RE``) that task 11.1 added to ``graph.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` resolves exactly as every sibling test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import _parse_levels_from_text  # noqa: E402


# ── Generators constrained to the multiplier input space ───────────────────────

# Field keyword that primes each extractor regex (entry / stop-loss / target).
_field_keyword = st.sampled_from([
    "entry",
    "stop",
    "stop-loss",
    "stop loss",
    "sl",
    "target",
    "take-profit",
    "take profit",
    "tp",
])

# Multiplier numbers as they appear in "<k>x ATR" rule prose.
_multiplier = st.sampled_from([1.0, 1.5, 2.0, 2.5, 3.0, 1.25, 0.5, 4.0])

# The multiplier token that flags the number as a multiple, not a price.
_mult_token = st.sampled_from(["x", "X", "\u00d7"])

# Whitespace optionally separating the number from the multiplier/ATR token.
_gap = st.sampled_from(["", " ", "  ", "\t"])

# A trailing phrase after the multiplier — realistic rule prose.
_tail = st.sampled_from(["ATR", "ATR below structure", "the range", "average range"])


@settings(max_examples=200)
@given(
    keyword=_field_keyword,
    mult=_multiplier,
    token=_mult_token,
    gap=_gap,
    tail=_tail,
)
def test_number_followed_by_multiplier_token_never_captured_as_price(
    keyword, mult, token, gap, tail
):
    """A number immediately followed by an x/X/× token is a multiplier and must
    never be captured as entry / stop_loss / take_profit."""
    mult_str = f"{mult:g}"
    text = (
        f"Standing aside — no edge. Rule: {keyword} >= {mult_str}{gap}{token} {tail}."
    )

    levels = _parse_levels_from_text(text) or {}

    for field in ("entry", "stop_loss", "take_profit"):
        assert levels.get(field) != mult, (
            f"'{mult_str}{gap}{token} {tail}' multiplier captured as "
            f"{field}={mult} from text: {text!r}"
        )


@settings(max_examples=200)
@given(keyword=_field_keyword, mult=_multiplier, gap=_gap)
def test_number_followed_by_atr_token_never_captured_as_price(keyword, mult, gap):
    """A number immediately followed by an ATR token is a multiplier and must
    never be captured as entry / stop_loss / take_profit."""
    mult_str = f"{mult:g}"
    text = f"No trade here. Rule: {keyword} at {mult_str}{gap}ATR from the low."

    levels = _parse_levels_from_text(text) or {}

    for field in ("entry", "stop_loss", "take_profit"):
        assert levels.get(field) != mult, (
            f"'{mult_str}{gap}ATR' multiplier captured as {field}={mult} "
            f"from text: {text!r}"
        )
