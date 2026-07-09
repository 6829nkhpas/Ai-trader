# Feature: deep-quant-runtime-hardening, Property 6: the prose level extractor
# rejects "Target N" ordinal indices as prices and captures the price that
# follows; a bare "target P" (no ordinal) still captures P.
"""R4 verification property test — level extractor rejects target ordinals.

Feature: deep-quant-runtime-hardening

Property 6 (Expected Behavior), Python ``graph._parse_levels_from_text`` seam:

    A "Target N: P" phrase names a target *index* N (an ordinal such as the
    ``1`` in "Target 1") followed by the actual take-profit PRICE P. The
    extractor must capture P as ``take_profit`` and must NEVER capture the
    ordinal N. A bare "target P" (no ordinal label) must still capture P.

    Validates: Requirements 4.3.

This is a dedicated VERIFICATION test: it encodes the CORRECT (fixed) behavior
and must PASS against the ordinal-consuming ``_TP_RE`` (the atomic
``(?:\\s*\\d{1,2}\\s*[:.)\\-])?`` group) that task 11.1 added to ``graph.py``.
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


# ── Generators constrained to the "Target N: P" input space ────────────────────

# The take-profit keyword that primes the target extractor regex.
_tp_keyword = st.sampled_from([
    "Target",
    "target",
    "TARGET",
    "take-profit",
    "take profit",
    "tp",
    "TP",
])

# A target ordinal / index — 1 to 2 digits (Target 1 .. Target 99).
_ordinal = st.integers(min_value=1, max_value=99)

# Delimiter separating the ordinal label from the price (matches the regex's
# atomic ordinal-consume class [:.)-]).
_delim = st.sampled_from([":", ".", ")", "-"])

# Whitespace variants around the delimiter — realistic prose spacing.
_gap = st.sampled_from(["", " ", "  "])

# A realistic take-profit PRICE — 4 to 6 digit integer, well clear of the 1-2
# digit ordinal space so a spurious ordinal capture is unambiguously detectable.
_price = st.integers(min_value=1000, max_value=999999)


@settings(max_examples=200)
@given(
    keyword=_tp_keyword,
    ordinal=_ordinal,
    delim=_delim,
    pre=_gap,
    post=_gap,
    price=_price,
)
def test_target_ordinal_never_captured_take_profit_is_price(
    keyword, ordinal, delim, pre, post, price
):
    """"Target N: P" captures P as take_profit and never the ordinal N."""
    text = (
        f"Holding for now. {keyword} {ordinal}{pre}{delim}{post}{price} "
        f"is the objective on a break."
    )

    levels = _parse_levels_from_text(text) or {}

    assert levels.get("take_profit") == float(price), (
        f"expected take_profit={price} from {text!r}, got "
        f"{levels.get('take_profit')!r}"
    )
    assert levels.get("take_profit") != float(ordinal), (
        f"ordinal {ordinal} captured as take_profit from {text!r}"
    )


@settings(max_examples=200)
@given(keyword=_tp_keyword, price=_price)
def test_bare_target_without_ordinal_still_captures_price(keyword, price):
    """A bare "target P" with no ordinal label still captures P."""
    text = f"No entry yet. {keyword} {price} on the upside if it clears resistance."

    levels = _parse_levels_from_text(text) or {}

    assert levels.get("take_profit") == float(price), (
        f"expected take_profit={price} from bare-target {text!r}, got "
        f"{levels.get('take_profit')!r}"
    )
