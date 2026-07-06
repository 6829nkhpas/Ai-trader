"""Property-based test for Debate_Stance parsing & round-trip (debate.py, task 2.2).

Feature: multi-agent-debate

This module implements design **Property 5: Debate_Stance parsing is well-formed
and round-trips**:

    For *any* raw role output -- mappings with arbitrary/garbled values, JSON
    object strings, arbitrary objects, ``None``, or malformed scalar types --
    ``parse_stance`` returns a ``DebateStance`` whose every field satisfies the
    documented invariants (``lean`` in ``DEBATE_LEANS``, ``strength`` an int in
    ``[STRENGTH_MIN, STRENGTH_MAX]``, ``arguments`` a list of strings, and
    ``biggest_risk`` a string), and for any well-formed stance the serialize /
    parse round-trip ``parse_stance(role, stance_to_dict(stance))`` preserves
    ``lean``, ``strength``, ``arguments`` and ``biggest_risk``.

Validates: Requirements 3.3.

The sys.path / import pattern mirrors the sibling ``test_session_*`` and
``test_debate_config_resolution_properties`` modules.
"""

import json
import os
import sys
from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (debate.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from debate import (  # noqa: E402
    DEBATE_LEANS,
    STRENGTH_MAX,
    STRENGTH_MIN,
    DebateStance,
    parse_stance,
    stance_to_dict,
)


# ─────────────────────────────────────────────────────────────────────────────
# Strategies for arbitrary, possibly-garbled raw role outputs
# ─────────────────────────────────────────────────────────────────────────────

# Leaf scalars covering the full degraded scalar space the parser may receive.
_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(allow_nan=True, allow_infinity=True, width=32),
    st.text(max_size=30),
)

# Arbitrary JSON-like nested values (lists/dicts of scalars) for fuzzing fields.
_arbitrary_values = st.recursive(
    _scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=8), children, max_size=4),
    ),
    max_leaves=8,
)


@st.composite
def _stance_like_dict(draw):
    """A dict that sometimes carries the recognized keys with garbled values."""
    data = {}
    if draw(st.booleans()):
        data["lean"] = draw(
            st.one_of(
                st.sampled_from(
                    ["long", "short", "neutral", "LONG", " Short ", "bull", "up", ""]
                ),
                _arbitrary_values,
            )
        )
    if draw(st.booleans()):
        data["strength"] = draw(
            st.one_of(
                st.integers(min_value=-1_000, max_value=1_000),
                st.floats(allow_nan=True, allow_infinity=True, width=32),
                st.text(max_size=12),
                st.booleans(),
                st.none(),
            )
        )
    if draw(st.booleans()):
        data["arguments"] = draw(_arbitrary_values)
    if draw(st.booleans()):
        data["biggest_risk"] = draw(_arbitrary_values)
    if draw(st.booleans()):
        data["available"] = draw(st.one_of(st.booleans(), _arbitrary_values))
    # Occasionally sprinkle unrelated keys to mimic noisy LLM output.
    if draw(st.booleans()):
        data[draw(st.text(min_size=1, max_size=6))] = draw(_arbitrary_values)
    return data


def _identifier_dicts():
    """Dicts whose keys are valid identifiers, for ``SimpleNamespace(**d)``."""
    return st.dictionaries(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8
        ),
        _arbitrary_values,
        max_size=4,
    )


def _raw_role_outputs():
    """Any raw role output the parser must defend against."""
    return st.one_of(
        st.none(),
        _scalars,
        _arbitrary_values,
        _stance_like_dict(),
        # JSON object strings (and occasionally non-object JSON / junk text).
        st.builds(json.dumps, _stance_like_dict()),
        st.builds(json.dumps, _arbitrary_values),
        st.text(max_size=30),
        # Arbitrary objects exposing a ``__dict__`` namespace.
        st.builds(lambda d: SimpleNamespace(**d), _identifier_dicts()),
    )


def _roles():
    """Arbitrary role identifiers (including degenerate ones)."""
    return st.one_of(
        st.sampled_from(["bull", "bear", "judge", "BULL", " Bear "]),
        st.text(max_size=10),
        st.none(),
        st.integers(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Strategy for well-formed stances (already normalized so the round-trip is exact)
# ─────────────────────────────────────────────────────────────────────────────

# Argument strings normalized exactly as ``parse_stance`` would store them:
# stripped and non-empty (so the serialize/parse round-trip is the identity).
_clean_arguments = st.lists(
    st.text(min_size=1, max_size=20).map(str.strip).filter(lambda s: s != ""),
    max_size=5,
)

# biggest_risk normalized as the parser stores it: a stripped string.
_clean_biggest_risk = st.text(max_size=40).map(str.strip)


@st.composite
def _well_formed_stances(draw):
    """A ``DebateStance`` whose fields already satisfy the parser's invariants."""
    return DebateStance(
        role=draw(st.sampled_from(["bull", "bear", "judge"])),
        lean=draw(st.sampled_from(list(DEBATE_LEANS))),
        strength=draw(st.integers(min_value=STRENGTH_MIN, max_value=STRENGTH_MAX)),
        arguments=draw(_clean_arguments),
        biggest_risk=draw(_clean_biggest_risk),
        available=draw(st.booleans()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: Debate_Stance parsing is well-formed and round-trips
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 5: Debate_Stance parsing is well-formed and round-trips
@settings(max_examples=100, deadline=None)
@given(role=_roles(), raw=_raw_role_outputs())
def test_property_5_parse_stance_is_well_formed(role, raw):
    """Validates: Requirements 3.3

    For ANY raw role output, ``parse_stance`` never raises and returns a
    ``DebateStance`` whose fields are all within their documented bounds.
    """
    stance = parse_stance(role, raw)

    assert isinstance(stance, DebateStance)

    # lean is exactly one of the recognized directional leans.
    assert stance.lean in DEBATE_LEANS, f"lean {stance.lean!r} not in {DEBATE_LEANS}"

    # strength is a real int (not bool) clamped into [STRENGTH_MIN, STRENGTH_MAX].
    assert isinstance(stance.strength, int) and not isinstance(stance.strength, bool)
    assert STRENGTH_MIN <= stance.strength <= STRENGTH_MAX, (
        f"strength {stance.strength} out of [{STRENGTH_MIN}, {STRENGTH_MAX}]"
    )

    # arguments is a list of strings.
    assert isinstance(stance.arguments, list)
    assert all(isinstance(arg, str) for arg in stance.arguments), (
        f"arguments must all be strings, got {stance.arguments!r}"
    )

    # biggest_risk is a string.
    assert isinstance(stance.biggest_risk, str)

    # role and available carry the documented types as well.
    assert isinstance(stance.role, str)
    assert isinstance(stance.available, bool)


# Feature: multi-agent-debate, Property 5: Debate_Stance parsing is well-formed and round-trips
@settings(max_examples=100, deadline=None)
@given(stance=_well_formed_stances())
def test_property_5_stance_serialize_parse_round_trips(stance):
    """Validates: Requirements 3.3

    For any well-formed stance, ``parse_stance(role, stance_to_dict(stance))``
    preserves ``lean``, ``strength``, ``arguments`` and ``biggest_risk``.
    """
    serialized = stance_to_dict(stance)
    assert isinstance(serialized, dict)

    # The serialized form must itself be JSON-serializable (it is stored in the
    # graph state and the defensibility record).
    json.dumps(serialized)

    reparsed = parse_stance(stance.role, serialized)

    assert reparsed.lean == stance.lean
    assert reparsed.strength == stance.strength
    assert reparsed.arguments == stance.arguments
    assert reparsed.biggest_risk == stance.biggest_risk
    # available is part of the serialized contract and round-trips too.
    assert reparsed.available == stance.available
