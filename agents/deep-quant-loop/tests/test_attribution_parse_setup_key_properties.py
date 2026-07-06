"""Property-based test for fingerprint parsing totality & determinism (attribution.py, task 2.2).

Feature: feature-attribution-pruning

This module implements design **Property 5: Fingerprint parsing is total and
deterministic**:

    For any string (valid setup_key, empty, malformed, or containing absent/
    `unknown` values), ``parse_setup_key`` returns a well-formed
    ``{dimension: value}`` mapping without raising and deterministically,
    treating absent/`unknown` values as the literal `unknown` and never crashing
    the surrounding report build on malformed input.

Validates: Requirements 1.3, 5.4.

The sys.path / import pattern mirrors ``tests/test_attribution_config_robustness_properties.py``
and the other property tests in this package.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from attribution import UNKNOWN_VALUE, parse_setup_key  # noqa: E402


# ── Strategies ────────────────────────────────────────────────────────────────
# A dimension name is a non-empty token with no ':' (the partition delimiter),
# no '|' (the token delimiter), and no surrounding whitespace (which the parser
# strips). This keeps the structured generator's EXPECTED dimension key equal to
# the literal we put in the string.
_DIM_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
_dimension = st.text(alphabet=_DIM_ALPHABET, min_size=1, max_size=6)

# A "normal" value: non-empty, may contain ':' (so we exercise the first-colon
# split, e.g. ``fc:aligned:strong`` -> value ``aligned:strong``), never contains
# '|', has no surrounding whitespace, and is not the literal ``unknown`` (any
# case) — so its expected parsed value is the value verbatim.
_VAL_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:."
_normal_value = st.text(alphabet=_VAL_ALPHABET, min_size=1, max_size=8).filter(
    lambda v: v.strip() and v.strip().lower() != UNKNOWN_VALUE
)

# Spellings that the parser must collapse to the literal "unknown": empty value,
# absent ':' (the whole token is the dimension), and the explicit "unknown" in
# mixed case / padded with whitespace.
_unknown_spelling = st.sampled_from(
    ["unknown", "UNKNOWN", "Unknown", "UnKnOwN", "  unknown  ", "unknown\t"]
)


@st.composite
def _structured_setup_key(draw):
    """Build a setup_key-like string with KNOWN expected ``{dimension: value}``.

    Returns ``(setup_key, expected)`` where each dimension is unique (so there is
    no last-occurrence-wins ambiguity) and falls into exactly one of four token
    kinds whose parsed value we can predict:

      * ``normal``  -> ``dim:value``           -> expected ``value``
      * ``empty``   -> ``dim:``                -> expected ``"unknown"``
      * ``absent``  -> ``dim``  (no ':')       -> expected ``"unknown"``
      * ``unknown`` -> ``dim:<unknown spelling>`` -> expected ``"unknown"``

    Empty tokens (from ``||`` / leading / trailing ``|`` / whitespace-only tokens)
    are interleaved at random; they must contribute nothing to the mapping.
    """
    # Unique dimensions via a dict keyed by dimension name.
    kinds = ["normal", "empty", "absent", "unknown"]
    spec = draw(
        st.dictionaries(
            keys=_dimension,
            values=st.sampled_from(kinds),
            max_size=10,
        )
    )

    tokens = []
    expected = {}
    for dim, kind in spec.items():
        if kind == "normal":
            value = draw(_normal_value)
            tokens.append(f"{dim}:{value}")
            expected[dim] = value
        elif kind == "empty":
            tokens.append(f"{dim}:")
            expected[dim] = UNKNOWN_VALUE
        elif kind == "absent":
            tokens.append(dim)
            expected[dim] = UNKNOWN_VALUE
        else:  # "unknown"
            tokens.append(f"{dim}:{draw(_unknown_spelling)}")
            expected[dim] = UNKNOWN_VALUE

    # Interleave empty/whitespace-only tokens that must contribute nothing.
    empties = draw(st.lists(st.sampled_from(["", "   ", "\t"]), max_size=4))
    combined = tokens + empties
    combined = draw(st.permutations(combined))

    return "|".join(combined), expected


# Arbitrary strings: unconstrained text plus a few hand-picked degenerate /
# malformed shapes that stress the tolerance rules.
_arbitrary_setup_key = st.one_of(
    st.text(max_size=60),
    st.sampled_from(
        [
            "",
            "   ",
            "\t\n",
            "|",
            "||",
            "a||b",
            ":",
            ":trend",
            "regime",
            "regime:",
            "regime:unknown",
            "dir:BUY|regime:trend-favorable|rs:leader-aligned",
            "fc:aligned:strong",
            "a:b|:|c",
            "|||",
            "x:|y:unknown|z",
        ]
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 (task 2.2): Fingerprint parsing is total and deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 5: For any string (valid setup_key, empty, malformed, or containing absent/unknown values), parse_setup_key returns a well-formed {dimension: value} mapping without raising and deterministically, treating absent/unknown values as the literal unknown and never crashing the surrounding report build on malformed input.
@settings(max_examples=200, deadline=None)
@given(setup_key=_arbitrary_setup_key)
def test_property_5_parse_total_wellformed_deterministic(setup_key):
    """Feature: feature-attribution-pruning, Property 5: parsing is total,
    well-formed, and deterministic over arbitrary strings.

    For ANY string: ``parse_setup_key`` never raises, returns a ``dict`` mapping
    ``str`` dimensions to non-empty ``str`` values, and yields an EQUAL result on
    a repeated call (deterministic, no ambient state).

    Validates: Requirements 1.3, 5.4
    """
    # Never raises (totality).
    result = parse_setup_key(setup_key)

    # Well-formed: a dict of str -> non-empty str. Values are never None/empty
    # because empty/absent/unknown values collapse to the literal "unknown".
    assert isinstance(result, dict)
    for dimension, value in result.items():
        assert isinstance(dimension, str)
        assert isinstance(value, str)
        assert value  # non-empty

    # Deterministic: a second parse of the same input is deep-equal.
    assert parse_setup_key(setup_key) == result


# Feature: feature-attribution-pruning, Property 5: For any string (valid setup_key, empty, malformed, or containing absent/unknown values), parse_setup_key returns a well-formed {dimension: value} mapping without raising and deterministically, treating absent/unknown values as the literal unknown and never crashing the surrounding report build on malformed input.
@settings(max_examples=200, deadline=None)
@given(case=_structured_setup_key())
def test_property_5_absent_unknown_map_to_literal_unknown(case):
    """Feature: feature-attribution-pruning, Property 5: absent / empty / explicit
    ``unknown`` values map to the literal ``"unknown"``.

    For a structured setup_key whose per-dimension expected value is known,
    ``parse_setup_key`` reproduces that mapping exactly: normal values pass
    through verbatim (split on the FIRST ':'), while absent ':' , empty values,
    and explicit ``unknown`` spellings (any case, padded) all collapse to the
    literal ``"unknown"`` — and empty tokens contribute nothing.

    Validates: Requirements 1.3, 5.4
    """
    setup_key, expected = case

    result = parse_setup_key(setup_key)

    assert result == expected
    # Determinism across the structured space too.
    assert parse_setup_key(setup_key) == result
