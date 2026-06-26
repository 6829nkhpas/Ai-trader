"""Property-based test that the classifier emits no trade decision (options_bias.py, task 2.8).

Feature: options-agent-integration

This module implements design **Property 19: The classifier emits no trade
decision (scope boundary)**:

    For ANY analytics input — well-formed, partially-formed, all-null, or
    outright malformed/garbage — and ANY proposed direction,
    ``options_bias.classify_options_bias`` returns ONLY an Options_Bias_Label.
    The label carries EXACTLY the label fields ``options_bias_state`` /
    ``alignment`` / ``signals`` and NEVER any trade-decision / action field — no
    ``action``, ``recommendation``, ``conviction``, ``conviction_score``,
    ``score``, ``entry``, ``stop_loss``, ``take_profit``, or ``decision`` key —
    and no string value anywhere within the result equals a BUY / SELL / HOLD
    action. ``options_bias_state`` is only a bias category
    (``bullish`` / ``bearish`` / ``neutral``), never a BUY/SELL/HOLD action. The
    Options_Bias_Classifier is a filter / context aid, not a trade generator.

Validates: Requirements 10.1.

The classifier is the single source of truth for the options-bias label, so this
property exercises ``classify_options_bias`` directly over a generator that spans
the full input space: each analytics field independently takes valid, ``null``,
or garbage values, the analytics container itself may be a non-dict, and the
proposed direction spans recognized / unrecognized / absent / non-string values
(including BUY/SELL/HOLD, which must never leak back out as a decision). The
sys.path / import pattern mirrors the sibling ``test_options_*_properties.py`` /
``test_of_*_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options_bias.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options_bias import (  # noqa: E402
    ALIGNMENT_VALUES,
    OPTIONS_BIAS_STATES,
    classify_options_bias,
    resolve_options_bias_config,
)

# Resolve config once (deterministic, env-independent for the default path).
_CONFIG = resolve_options_bias_config()

_VALID_STATES = set(OPTIONS_BIAS_STATES)      # {"bullish", "bearish", "neutral"}
_VALID_ALIGNMENTS = set(ALIGNMENT_VALUES)     # {"aligned", "misaligned", "neutral"}

# The label is EXACTLY these three fields — nothing more (scope boundary).
_ALLOWED_TOP_LEVEL_KEYS = frozenset({"options_bias_state", "alignment", "signals"})

# Trade-decision / action fields the label must NEVER carry, at ANY nesting level
# (Requirement 10.1). The classifier emits a label only — never a decision.
_FORBIDDEN_KEYS = frozenset(
    {
        "action",
        "recommendation",
        "conviction",
        "conviction_score",
        "score",
        "entry",
        "stop_loss",
        "take_profit",
        "decision",
        "trade",
    }
)

# BUY / SELL / HOLD action words that must not appear as a value anywhere in the
# result (compared case-insensitively after stripping).
_ACTION_WORDS = frozenset({"BUY", "SELL", "HOLD"})


# ── Smart generators spanning the FULL analytics input space ──────────────────
# Each numeric analytic must tolerate finite numbers, ``None``, non-finite floats,
# and outright non-numeric junk, so the scalar generator mixes all of them.
_weird_number = st.one_of(
    st.none(),
    st.floats(min_value=-1_000_000.0, max_value=1_000_000.0,
              allow_nan=False, allow_infinity=False),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.just(0.0),
    st.text(max_size=4),   # non-numeric junk
    st.booleans(),         # bool is explicitly excluded by the classifier
)

# PCR(OI) leans positive in practice but the classifier must tolerate anything.
_weird_pcr = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    _weird_number,
)

# An OI-buildup label: the five F2 categories plus junk / non-strings. A label
# value could itself collide with an action word, so include BUY/SELL/HOLD-ish
# junk to prove it would be caught if it ever leaked into the output.
_buildup_label = st.one_of(
    st.sampled_from([
        "long_buildup", "short_buildup", "short_covering",
        "long_unwinding", "neutral",
    ]),
    st.sampled_from(["BUY", "SELL", "HOLD"]),
    st.text(max_size=5),
    st.none(),
    st.integers(),
)

# The aggregate oi_buildup field: a well-formed {"call","put"} object, a partial
# object, a junk dict, a non-dict, or absent.
_weird_oi_buildup = st.one_of(
    st.none(),
    st.fixed_dictionaries({"call": _buildup_label, "put": _buildup_label}),
    st.dictionaries(keys=st.text(max_size=4), values=_buildup_label, max_size=3),
    st.text(max_size=4),
    st.integers(),
)

# The oi_walls field: a well-formed {"support","resistance"} object, partial /
# junk dict, a non-dict, or absent.
_weird_oi_walls = st.one_of(
    st.none(),
    st.fixed_dictionaries({"support": _weird_number, "resistance": _weird_number}),
    st.dictionaries(keys=st.text(max_size=4), values=_weird_number, max_size=3),
    st.text(max_size=4),
    st.integers(),
)

# The iv_skew field: a well-formed object carrying put_minus_call, a junk dict, a
# non-dict, or absent.
_weird_iv_skew = st.one_of(
    st.none(),
    st.fixed_dictionaries({"put_minus_call": _weird_number}),
    st.dictionaries(keys=st.text(max_size=4), values=_weird_number, max_size=3),
    st.text(max_size=4),
    st.integers(),
)


@st.composite
def _weird_analytics(draw):
    """An analytics dict spanning well-formed, partial, all-null, and garbage.

    Every field independently takes a valid / null / garbage value so the whole
    input space — including a dict missing fields entirely — is reachable.
    """
    base = {
        "pcr_oi": draw(_weird_pcr),
        "oi_buildup": draw(_weird_oi_buildup),
        "max_pain": draw(_weird_number),
        "spot": draw(_weird_number),
        "oi_walls": draw(_weird_oi_walls),
        "iv_skew": draw(_weird_iv_skew),
        "futures_basis": draw(_weird_number),
    }
    # Randomly drop a subset of keys so "structurally absent" fields are exercised.
    drop = draw(st.sets(st.sampled_from(list(base.keys())), max_size=len(base)))
    return {k: v for k, v in base.items() if k not in drop}


# The analytics container itself may be a non-dict (malformed input) — the
# classifier must still return a neutral label rather than raising.
_analytics_or_garbage = st.one_of(
    _weird_analytics(),
    st.none(),
    st.text(max_size=5),
    st.integers(),
    st.lists(st.integers(), max_size=3),
)

# A proposed direction spanning recognized / unrecognized / absent / non-string.
# BUY/SELL/HOLD are deliberately included: even when supplied as input, they must
# never leak back out as a decision value.
_weird_direction = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", " hold ", "FLAT", ""]),
    st.text(max_size=5),
    st.integers(),
)


def _walk_strings_and_keys(obj):
    """Yield ``("key", k)`` for every mapping key and ``("value", v)`` for every
    leaf value reached by recursively walking dicts / lists / tuples in ``obj``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("key", k)
            yield from _walk_strings_and_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_strings_and_keys(item)
    else:
        yield ("value", obj)


# ─────────────────────────────────────────────────────────────────────────────
# Property 19 (task 2.8): the classifier emits no trade decision (scope boundary)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 19: The classifier emits no trade decision (scope boundary)
@settings(max_examples=300, deadline=None)
@given(analytics=_analytics_or_garbage, proposed_direction=_weird_direction)
def test_property_19_classifier_emits_no_trade_decision(analytics, proposed_direction):
    """Feature: options-agent-integration, Property 19: The classifier emits no
    trade decision (scope boundary).

    For any analytics input (well-formed or malformed) and any proposed
    direction, ``classify_options_bias`` returns ONLY an Options_Bias_Label whose
    top-level keys are exactly ``options_bias_state`` / ``alignment`` /
    ``signals``; the result carries no trade-decision / action key at any nesting
    level and no BUY/SELL/HOLD action value anywhere within it. The
    ``options_bias_state`` is only a bias category, never a BUY/SELL/HOLD action.

    Validates: Requirements 10.1
    """
    label = classify_options_bias(analytics, _CONFIG, proposed_direction=proposed_direction)

    # The classifier only ever emits a dict (a label).
    assert isinstance(label, dict), f"expected a label dict, got {label!r}"

    # The label carries EXACTLY the three label fields — no extra top-level field
    # (which could smuggle in a trade decision). Scope boundary (Requirement 10.1).
    assert set(label.keys()) == _ALLOWED_TOP_LEVEL_KEYS, (
        f"label top-level keys {set(label.keys())} != {set(_ALLOWED_TOP_LEVEL_KEYS)}"
    )

    # ``options_bias_state`` is only a bias category — never a BUY/SELL/HOLD action.
    state = label["options_bias_state"]
    assert state in _VALID_STATES, (
        f"options_bias_state {state!r} is not a bias category {_VALID_STATES}"
    )
    assert (
        isinstance(state, str) and state.strip().upper() not in _ACTION_WORDS
    ), f"options_bias_state {state!r} is a trade action, not a bias category"

    # ``alignment`` is only an alignment category — never an action.
    alignment = label["alignment"]
    assert alignment in _VALID_ALIGNMENTS, (
        f"alignment {alignment!r} is not an alignment category {_VALID_ALIGNMENTS}"
    )

    # No trade-decision / action field appears at any nesting level (Req 10.1).
    for kind, item in _walk_strings_and_keys(label):
        if kind == "key" and isinstance(item, str):
            assert item.lower() not in _FORBIDDEN_KEYS, (
                f"forbidden trade-decision key {item!r} present in label: {label!r}"
            )

    # No string value anywhere within the result equals a BUY/SELL/HOLD action
    # (Requirement 10.1) — even though a BUY/SELL/HOLD proposed_direction may have
    # been supplied as input, it never leaks out as a decision value.
    for kind, item in _walk_strings_and_keys(label):
        if kind == "value" and isinstance(item, str):
            assert item.strip().upper() not in _ACTION_WORDS, (
                f"BUY/SELL/HOLD action value {item!r} present in label: {label!r}"
            )
